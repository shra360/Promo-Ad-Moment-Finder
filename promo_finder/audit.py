"""Dataset audit (Phase 0). Usage: python -m promo_finder.audit <folder> [out_dir]

Deterministic checks only. Prints a summary and writes per-episode stats,
issues, speaker labels and cross-episode repeated lines to out_dir.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .parse import parse_episode

EPS = 0.05  # tolerance in seconds for touching/rounding


def norm_line(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def episode_stats(ep):
    blocks = list(ep.blocks())
    kinds = Counter(b.kind for b in blocks)
    issues = list(ep.issues)
    dur = max((s.end or 0) for s in ep.scenes) if ep.scenes else 0.0

    prev_end = 0.0
    for s in ep.scenes:
        if s.start is not None and abs(s.start - prev_end) > EPS:
            issues.append(f"scene {s.scene_no}: starts {s.start:.2f}s, previous ended {prev_end:.2f}s")
        prev_end = s.end or prev_end
        for b in s.blocks:
            if b.start is None or b.end is None:
                continue
            if b.end <= b.start:
                issues.append(f"{b.block_id}: end <= start")
            if s.start is not None and s.end is not None and (b.start < s.start - EPS or b.end > s.end + EPS):
                issues.append(f"{b.block_id}: [{b.start:.2f},{b.end:.2f}] outside scene [{s.start:.2f},{s.end:.2f}]")
            if b.kind == "dialogue" and not b.text.startswith('"'):
                issues.append(f"{b.block_id}: dialogue text not quoted")

    # out-of-order blocks within the file (parallel tracks are listed, not sorted)
    starts = [b.start for b in blocks if b.start is not None]
    out_of_order = sum(1 for a, c in zip(starts, starts[1:]) if c < a - EPS)

    # camera track continuity
    cams = sorted([b for b in blocks if b.kind == "camera" and b.start is not None], key=lambda b: b.start)
    cam_gaps = [round(n.start - p.end, 2) for p, n in zip(cams, cams[1:]) if n.start - p.end > EPS]
    cam_overlaps = [round(p.end - n.start, 2) for p, n in zip(cams, cams[1:]) if p.end - n.start > EPS]

    # dialogue overlaps (two lines sounding at once) and dialogue that straddles a camera cut
    dl = sorted([b for b in blocks if b.kind == "dialogue" and b.start is not None], key=lambda b: b.start)
    dlg_overlaps = sum(1 for p, n in zip(dl, dl[1:]) if p.end - n.start > EPS)
    cut_points = {round(c.end, 2) for c in cams}
    straddle = sum(1 for d in dl if any(d.start + EPS < c < d.end - EPS for c in cut_points))
    dialogue_secs = sum(d.end - d.start for d in dl)

    return {
        "episode": ep.episode,
        "file_id": ep.file_episode_id,
        "duration_s": round(dur, 2),
        "scenes": len(ep.scenes),
        "blocks": len(blocks),
        **{f"n_{k}": kinds.get(k, 0) for k in ("camera", "action", "sfx", "dialogue", "other")},
        "speakers": len({d.speaker for d in dl}),
        "dialogue_coverage": round(dialogue_secs / dur, 2) if dur else 0,
        "out_of_order_blocks": out_of_order,
        "camera_gaps": len(cam_gaps),
        "camera_overlaps": len(cam_overlaps),
        "dialogue_overlaps": dlg_overlaps,
        "dialogue_straddling_cuts": straddle,
        "has_logline": bool(ep.logline),
        "has_continuity": bool(ep.continuity_notes),
        "n_issues": len(issues),
    }, issues


def main(folder: str, out_dir: str = "audit_out"):
    files = sorted(Path(folder).glob("episode-*.txt"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows, all_issues = [], {}
    speakers = Counter()
    line_index = defaultdict(set)
    eps = []
    for f in files:
        ep = parse_episode(f)
        eps.append(ep)
        row, issues = episode_stats(ep)
        rows.append(row)
        all_issues[f.name] = issues
        for b in ep.blocks():
            if b.kind == "dialogue":
                speakers[b.speaker] += 1
                n = norm_line(b.text)
                if len(n.split()) >= 3:
                    line_index[n].add(ep.episode)

    nums = sorted(r["episode"] for r in rows if r["episode"] is not None)
    expected = set(range(1, (max(nums) if nums else 0) + 1))
    repeated = {k: sorted(v) for k, v in line_index.items() if len(v) > 1}

    with open(out / "per_episode.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    (out / "issues.json").write_text(json.dumps(all_issues, indent=2), encoding="utf-8")
    (out / "speaker_labels.json").write_text(json.dumps(speakers.most_common(), indent=2), encoding="utf-8")
    (out / "repeated_lines.json").write_text(json.dumps(repeated, indent=2), encoding="utf-8")
    (out / "parsed").mkdir(exist_ok=True)
    for ep in eps:
        (out / "parsed" / f"episode-{ep.episode:03d}.json").write_text(json.dumps(ep.to_dict(), indent=1), encoding="utf-8")

    total = sum(r["duration_s"] for r in rows)
    print(f"files: {len(files)}  episodes: {nums[:3]}..{nums[-3:]}  missing: {sorted(expected - set(nums))}")
    print(f"duplicate episode numbers: {[n for n, c in Counter(nums).items() if c > 1]}")
    print(f"runtime: total {total/60:.1f} min, per-episode min {min(r['duration_s'] for r in rows)}s "
          f"max {max(r['duration_s'] for r in rows)}s")
    print(f"blocks: {sum(r['blocks'] for r in rows)}  dialogue lines: {sum(r['n_dialogue'] for r in rows)}  "
          f"distinct speaker labels: {len(speakers)}")
    print(f"structural issues: {sum(len(v) for v in all_issues.values())} across "
          f"{sum(1 for v in all_issues.values() if v)} files")
    print(f"dialogue lines repeated across episodes: {len(repeated)}")
    for r in rows:
        print(json.dumps(r))
    for name, iss in all_issues.items():
        for i in iss[:20]:
            print(f"  ISSUE {name}: {i}")


if __name__ == "__main__":
    main(*sys.argv[1:])
