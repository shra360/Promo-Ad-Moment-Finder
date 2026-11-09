"""Stress tests: where does the logic break on inputs that differ from the 50-episode pack?

  python eval/stress.py --bench <reelbench dir> [--real data/raw]

1. Format drift  - rewrite episodes in plausible alternative spellings of the same format and check that the
                   parser extracts exactly the same blocks (type, times, speaker).
2. Time shift    - prepend silence: every pick should move by exactly the shift (content, not position).
3. Scene swap    - play scene 2 before scene 1: do the picks follow the story beats to their new times?
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from promo_finder.heuristic import propose  # noqa: E402
from promo_finder.parse import parse_episode  # noqa: E402

TS = re.compile(r"(\d{2}):(\d{2})\.(\d{2})")


def sig(ep):
    return [(b.kind, round(b.start or -1, 2), round(b.end or -1, 2), b.speaker) for b in ep.blocks()]


def fmt(t: float) -> str:
    m, s = divmod(round(t, 2), 60)
    return f"{int(m):02d}:{s:05.2f}"


def shift_text(txt: str, dt: float) -> str:
    return TS.sub(lambda m: fmt(int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 100 + dt), txt)


VARIANTS = {
    "Windows line endings (CRLF)": lambda t: t.replace("\n", "\r\n"),
    "lower-case track labels (**Camera:**)": lambda t: re.sub(r"\*\*(CAMERA|ACTION|SOUND FX):\*\*",
                                                             lambda m: f"**{m.group(1).title()}:**", t),
    "no bold markers (CAMERA:)": lambda t: re.sub(r"^\*\*(.+?):\*\*\s*$", r"\1:", t, flags=re.M),
    "SFX / SOUND EFFECT label": lambda t: t.replace("**SOUND FX:**", "**SFX:**"),
    "times without leading zero (0:39.30)": lambda t: re.sub(r"\((\d{2}):", lambda m: f"({int(m.group(1))}:", t),
    "times without parentheses": lambda t: re.sub(r"Start: \(([\d:.]+)\) \| End: \(([\d:.]+)\)", r"Start: \1 | End: \2", t),
    "times as HH:MM:SS.ff (00:00:39.30)": lambda t: re.sub(r"\((\d{2}:\d{2}\.\d{2})\)", r"(00:\1)", t),
    "en dash instead of pipe (Start: (..) – End: (..))": lambda t: t.replace(") | End: (", ") – End: ("),
    "speaker extension (CONT'D)": lambda t: re.sub(r"^\*\*([A-Z][A-Z .'\-]+) \(", r"**\1 (CONT'D) (", t, flags=re.M),
    "dialogue in smart quotes": lambda t: re.sub(r'^"(.*)"$', "“\\1”", t, flags=re.M),
    "extra blank lines and trailing spaces": lambda t: re.sub(r"\n", "  \n\n", t),
    "missing 'Actions and dialogue:' line": lambda t: t.replace("Actions and dialogue:\n", ""),
    "missing --- SCRIPT END ---": lambda t: t.replace("--- SCRIPT END ---", ""),
    "UTF-8 byte-order mark": lambda t: "﻿" + t,
}


def format_drift(files: list[Path]) -> list[dict]:
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn in VARIANTS.items():
            ok = 0
            first_diff = ""
            for f in files:
                raw = f.read_text(encoding="utf-8")
                g = Path(tmp) / f.name
                g.write_text(fn(raw), encoding="utf-8", newline="")
                a, b = sig(parse_episode(f)), sig(parse_episode(g))
                if a == b:
                    ok += 1
                elif not first_diff:
                    first_diff = f"{f.parent.name}/{f.name}: {len(a)} blocks -> {len(b)} parsed"
            out.append({"variant": name, "identical": ok, "files": len(files), "example": first_diff})
    return out


def picks(ep, n=4):
    """The baseline's proposals as (start time of hook block, end time of cliffhanger block)."""
    bm = ep.block_map()
    return [(bm[p["hook_block"]].start, bm[p["cliffhanger_block"]].end) for p in propose(ep, n)]


def time_shift(files: list[Path], dt: float = 13.7) -> dict:
    exact = moved = 0
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            g = Path(tmp) / f.name
            g.write_text(shift_text(f.read_text(encoding="utf-8"), dt), encoding="utf-8")
            a, b = picks(parse_episode(f)), picks(parse_episode(g))
            moved += len(a)
            exact += sum(1 for (x0, x1), (y0, y1) in zip(a, b) if abs(y0 - x0 - dt) < 0.02 and abs(y1 - x1 - dt) < 0.02)
    return {"shift_s": dt, "picks": moved, "moved_exactly": exact}


def scene_swap(bench: Path, files: list[Path]) -> dict:
    """Swap the two scenes of each benchmark episode and re-time them; the GT windows move with their scene."""
    gt = json.loads((bench / "annotations" / "promo_ground_truth.json").read_text(encoding="utf-8"))
    gtm = {a["scriptFile"]: a for a in gt["annotations"]}
    hit_orig = hit_swap = total = 0
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            rel = f"scripts/{f.parent.name}/{f.name}"
            if rel not in gtm:
                continue
            raw = f.read_text(encoding="utf-8")
            ep = parse_episode(f)
            if len(ep.scenes) != 2:
                continue
            body = raw.split("--- SCRIPT START ---")[1].split("--- SCRIPT END ---")[0]
            parts = re.split(r"(?=^SCENE \d+\s*$)", body, flags=re.M)
            parts = [p for p in parts if p.strip().startswith("SCENE")]
            s1, s2 = ep.scenes
            d1, d2 = s1.end - s1.start, s2.end - s2.start
            new2 = shift_text(parts[1], -s2.start).replace("SCENE 02", "SCENE 01", 1)
            new1 = shift_text(parts[0], d2).replace("SCENE 01", "SCENE 02", 1)
            swapped = raw.split("--- SCRIPT START ---")[0] + "--- SCRIPT START ---\n\n" + new2 + new1 + "--- SCRIPT END ---\n"
            g = Path(tmp) / f.name
            g.write_text(swapped, encoding="utf-8")
            ep2 = parse_episode(g)

            def moved(t):   # where a GT time lands after the swap
                return t - s2.start if t >= s2.start else t + d2

            for m in gtm[rel]["promoMoments"]:
                a, b = m["startSec"], m["endSec"]
                total += 1
                hit_orig += any(min(y, b) - max(x, a) >= 0.5 * (b - a) for x, y in picks(ep))
                a2, b2 = moved(a), moved(b)
                hit_swap += any(min(y, b2) - max(x, a2) >= 0.5 * (b2 - a2) for x, y in picks(ep2))
    return {"gt_windows": total, "covered_original": hit_orig, "covered_after_swap": hit_swap}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", required=True)
    ap.add_argument("--real", default=None)
    a = ap.parse_args()
    bench = Path(a.bench)
    bfiles = sorted((bench / "scripts").rglob("episode-*.txt"))
    sample = bfiles[::8]                         # 20 benchmark episodes across the 5 shows
    real = sorted(Path(a.real).glob("*.txt")) if a.real else []
    res = {"format_drift": format_drift(sample + real), "time_shift": time_shift(sample + real[:10]),
           "scene_swap": scene_swap(bench, bfiles)}
    print(json.dumps(res, indent=1))
    (ROOT / "bench").mkdir(exist_ok=True)
    (ROOT / "bench" / "stress.json").write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
