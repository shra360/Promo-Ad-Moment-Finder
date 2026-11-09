"""Sensationality mode: grade every moment for heat, then find the hottest 20-60 s windows.

1. Grade.  Every screenplay block gets a 0-10 sensationality score. Grader, in order of preference:
           a) the trained model in model/ (DeBERTa-v3-base fine-tuned on Kaggle on Gemini's grades of
              MovieSum + Kuku lines; 8 outputs: overall + 7 sub-scores),
           b) Gemini grading every block directly,
           c) a keyword grader (no key, no model).
2. Curve.  Code turns block grades into a heat curve over time (0.5 s steps, the hottest block covering
           each step), so a burst of fast camera cuts does not count more than one strong line.
3. Search. Every window between two clean cuts (no one mid-line) that lasts 20-60 s is scored:
             0.45 x mean heat + 0.20 x peak + 0.20 x hook (hottest point in the first 3 s)
             + 0.15 x ending (hottest point in the last 5 s), scaled by a preference for ~40 s.
4. Final.  With a key, Gemini assesses each episode's best candidate windows as whole clips, grades
           them 0-10 and may tighten the start/end block; code snaps those to clean cuts and enforces 20-60 s.
           Final score = 0.6 x Gemini's grade + 0.4 x the heat formula.
5. Pick.   The top 3 windows of the show (at most one per episode) and the top 3 in every episode
           (overlap is allowed, step by step, only when an episode is too short for 3 separate ones).
"""
from __future__ import annotations

import csv
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .cuts import clean_cut_points, windows
from .heuristic import block_score
from .parse import Episode
from .render import block_line, sorted_blocks
from .schemas import fmt_ts

DIMS = ("emotion", "conflict", "shock", "physical", "humiliation", "open_loop")
STEP = 0.5
HEATED = {
    "slap": r"\bslap", "punch/strike": r"\b(punch|strikes?|hits? (him|her)|backhand)",
    "shove/grab": r"\b(shoves?|grabs?|seizes?|yanks?|pins?)\b", "blood/wound": r"\b(blood|bleed|wound|gash)",
    "scream/shout": r"\b(scream|shriek|shout|yell|roar)", "weapon": r"\b(knife|dagger|sword|gun|pistol|blade)",
    "collapse/fall": r"\b(collapses?|faints?|falls?|drops to)", "kiss": r"\bkiss",
    "public humiliation": r"\b(humiliat|kneel|beg|mock|laugh at)", "threat": r"\b(kill|destroy|ruin|hang|die)\b",
}


# ------------------------------------------------------------------ 1. grade
def keyword_grades(ep: Episode) -> dict[str, dict]:
    out = {}
    for b in sorted_blocks(ep):
        raw = block_score(b)
        tags = [k for k, pat in HEATED.items() if re.search(pat, (b.text + " " + " ".join(b.delivery)).lower())]
        score = min(10.0, 1.4 * raw + 1.5 * bool({"slap", "punch/strike", "blood/wound", "weapon"} & set(tags)))
        if score >= 0.5 or tags:
            out[b.block_id] = {"score": round(score, 1), "tags": tags, "dims": {}}
    return out


MODEL_DIR = Path(__file__).resolve().parent.parent / "model"


def unit_text(b) -> str:
    """Same rendering as the training corpus (sensational/parser.py::unit_text in the training project)."""
    t = " ".join(b.text.split())
    if b.kind == "dialogue":
        d = f" ({', '.join(b.delivery)})" if b.delivery else ""
        return f"{b.speaker}{d}: {t}"
    return {"camera": "[CAMERA] ", "action": "[ACTION] ", "sfx": "[SOUND] "}.get(b.kind, "") + t


_MODEL = {}


def model_available(model_dir: Path = MODEL_DIR) -> bool:
    return (Path(model_dir) / "config.json").exists()


def model_grades(ep: Episode, model_dir: Path = MODEL_DIR) -> dict[str, dict]:
    """Score blocks with the fine-tuned encoder (outputs in [0, 1]: overall, then 7 sub-scores)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    key = str(model_dir)
    if key not in _MODEL:
        meta_f = Path(model_dir) / "sensationality.json"
        meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
        tok = AutoTokenizer.from_pretrained(model_dir)
        mdl = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
        _MODEL[key] = (tok, mdl, meta.get("labels", ["overall", "conflict", "shock", "emotion", "physicality",
                                                     "desire", "status", "open_loop"]), int(meta.get("max_len", 128)))
    tok, mdl, labels, max_len = _MODEL[key]
    blocks = sorted_blocks(ep)
    out = {}
    with torch.no_grad():
        for i in range(0, len(blocks), 32):
            chunk = blocks[i:i + 32]
            x = tok([unit_text(b) for b in chunk], truncation=True, max_length=max_len, padding=True, return_tensors="pt")
            y = mdl(**x).logits.clamp(0, 1).tolist()
            for b, row in zip(chunk, y):
                dims = {lab: round(v * 3, 1) for lab, v in zip(labels[1:], row[1:])}
                tags = [k for k, pat in HEATED.items() if re.search(pat, b.text.lower())]
                if row[0] * 10 >= 0.5:
                    out[b.block_id] = {"score": round(row[0] * 10, 1), "tags": tags, "dims": dims}
    return out


def _check(d: dict, ids: set[str]) -> list[str]:
    rows = d.get("blocks")
    if not isinstance(rows, list) or not rows:
        return ["'blocks' must be a non-empty list"]
    bad = sorted({str(r.get("block")) for r in rows if isinstance(r, dict) and r.get("block") not in ids})
    probs = [f"block IDs not in this episode: {', '.join(bad[:10])}"] if bad else []
    if any(not isinstance(r, dict) or not isinstance(r.get("score"), (int, float)) for r in rows):
        probs.append("every entry needs a numeric 'score' from 0 to 10")
    return probs


def llm_grades(llm, ep: Episode, aliases: dict | None = None) -> dict[str, dict]:
    ids = {b.block_id for b in sorted_blocks(ep)}
    text = "\n".join(block_line(b, aliases) for b in sorted_blocks(ep))
    r = llm.complete_json("sensational", {"episode": text, "n": ep.episode, "logline": ep.logline or "-"},
                          check=lambda d: _check(d, ids), tag=f"ep{ep.episode}")
    out = {}
    for row in r["data"].get("blocks", []):
        if not isinstance(row, dict) or row.get("block") not in ids:
            continue
        try:
            score = max(0.0, min(10.0, float(row.get("score", 0))))
        except (TypeError, ValueError):
            continue
        dims = {k: row[k] for k in DIMS if isinstance(row.get(k), (int, float))}
        tags = [str(t) for t in row.get("tags", []) if isinstance(t, (str, int))] if isinstance(row.get("tags"), list) else []
        out[row["block"]] = {"score": round(score, 1), "tags": tags, "dims": dims, "why": str(row.get("why", ""))[:160]}
    return out


# ------------------------------------------------------------------ 2. curve
def heat_curve(ep: Episode, grades: dict) -> list[float]:
    n = int(math.ceil(ep.duration / STEP)) + 1
    curve = [0.0] * n
    for b in sorted_blocks(ep):
        g = grades.get(b.block_id)
        if not g or g["score"] <= 0:
            continue
        for i in range(int(b.start / STEP), min(n, int(math.ceil(b.end / STEP)))):
            curve[i] = max(curve[i], g["score"])
    return curve


# ------------------------------------------------------------------ 3. search
def length_pref(L: float, ideal: float, lo: float, hi: float) -> float:
    span = max(ideal - lo, hi - ideal)
    return max(0.0, 1 - ((L - ideal) / span) ** 2)


def score_windows(ep: Episode, curve: list[float], cfg: dict) -> list[dict]:
    lo, hi, ideal = cfg["min_total"], cfg["max_total"], cfg.get("ideal_length", 40.0)
    pre = [0.0]
    for v in curve:
        pre.append(pre[-1] + v)
    seg = lambda a, b: curve[int(a / STEP):max(int(a / STEP) + 1, int(b / STEP))]
    out = []
    for a, b in windows(clean_cut_points(ep), lo, hi):
        i, j = int(a / STEP), max(int(a / STEP) + 1, int(b / STEP))
        mean = (pre[j] - pre[i]) / (j - i)
        peak = max(seg(a, b))
        hook = max(seg(a, min(b, a + 3)))
        end = max(seg(max(a, b - 5), b))
        s = (0.45 * mean + 0.20 * peak + 0.20 * hook + 0.15 * end) * (0.85 + 0.15 * length_pref(b - a, ideal, lo, hi))
        out.append({"episode": ep.episode, "start": round(a, 2), "end": round(b, 2), "duration": round(b - a, 2),
                    "score": round(s, 3), "mean_heat": round(mean, 2), "peak": peak, "hook": hook, "ending": end})
    return out


def pick(cands: list[dict], k: int, max_per_episode: int | None, max_overlap: float = 0.0) -> list[dict]:
    """Greedy best-first. max_overlap = share of the shorter window two picks may share (0 = none)."""
    chosen, per = [], {}
    for c in sorted(cands, key=lambda c: (-c["score"], abs(c["duration"] - 40))):
        if max_per_episode and per.get(c["episode"], 0) >= max_per_episode:
            continue
        if any(c["episode"] == x["episode"] and min(c["end"], x["end"]) - max(c["start"], x["start"])
               > max_overlap * min(c["duration"], x["duration"]) + 1e-6 for x in chosen):
            continue
        chosen.append(c)
        per[c["episode"]] = per.get(c["episode"], 0) + 1
        if len(chosen) == k:
            break
    return chosen


def describe(ep: Episode, w: dict, grades: dict, aliases=None) -> dict:
    inside = [b for b in sorted_blocks(ep) if w["start"] - 0.05 <= b.start < w["end"] - 0.05]
    hot = sorted([b for b in inside if b.block_id in grades], key=lambda b: -grades[b.block_id]["score"])
    first = next((b for b in inside if b.kind != "camera"), None)
    last = next((b for b in reversed(inside) if b.kind != "camera"), None)
    tags = sorted({t for b in inside for t in grades.get(b.block_id, {}).get("tags", [])})
    dims = {}
    for d in DIMS:
        vals = [grades[b.block_id]["dims"][d] for b in inside if d in grades.get(b.block_id, {}).get("dims", {})]
        if vals:
            dims[d] = round(max(vals), 1)
    q = lambda b: " ".join(b.text.split())[:140] if b else ""
    return {**w, "start_ts": fmt_ts(w["start"]), "end_ts": fmt_ts(w["end"]), "tags": tags, "dimensions": dims,
            "opens_on": q(first), "ends_on": q(last),
            "hottest_moments": [{"block": b.block_id, "at": fmt_ts(b.start), "score": grades[b.block_id]["score"],
                                 "text": q(b), "why": grades[b.block_id].get("why", "")} for b in hot[:3]]}


# ------------------------------------------------------------------ 4. final assessment by the LLM
def finalize(llm, ep: Episode, cands: list[dict], cfg: dict) -> list[dict]:
    """Gemini grades the candidate windows as whole clips and may tighten their edges; code keeps every
    time on a clean cut and inside 20-60 s."""
    from .assemble import snap_end, snap_start
    if not cands:
        return cands
    bm = ep.block_map()
    cuts = clean_cut_points(ep)
    parts = []
    for i, w in enumerate(cands, 1):
        lines = [block_line(b, rel_to=b.start - (b.start - w["start"])) for b in sorted_blocks(ep)
                 if w["start"] - 0.05 <= b.start < w["end"] - 0.05]
        parts.append(f"=== W{i} ({w['duration']:.1f} s, starts at {fmt_ts(w['start'])})\n" + "\n".join(lines))
    ids = {f"W{i}" for i in range(1, len(cands) + 1)}

    def check(d):
        rows = d.get("windows")
        if not isinstance(rows, list) or not rows:
            return ["'windows' must list every candidate"]
        got = {str(r.get("id")) for r in rows if isinstance(r, dict)}
        return [f"missing windows: {', '.join(sorted(ids - got))}"] if ids - got else []

    r = llm.complete_json("window_judge", {"n": ep.episode, "logline": ep.logline or "-", "count": len(cands),
                                           "windows": "\n\n".join(parts)}, check=check, tag=f"ep{ep.episode}-final")
    lo, hi = cfg["min_total"], cfg["max_total"]
    out = []
    for i, w in enumerate(cands, 1):
        row = next((x for x in r["data"].get("windows", []) if isinstance(x, dict) and str(x.get("id")) == f"W{i}"), {})
        try:
            grade = max(0.0, min(10.0, float(row.get("score", 0))))
        except (TypeError, ValueError):
            grade = 0.0
        w = dict(w, llm_grade=grade, llm_why=str(row.get("why", ""))[:200], formula_score=w["score"])
        sb, eb = bm.get(str(row.get("start_block", ""))), bm.get(str(row.get("end_block", "")))
        if sb and eb and sb.start is not None and eb.end is not None:
            a, b = snap_start(sb.start, cuts), snap_end(max(eb.end, sb.end), cuts)
            if a is not None and b is not None and lo - 0.05 <= b - a <= hi + 0.05 \
                    and w["start"] - 0.05 <= a and b <= w["end"] + 0.05:
                w.update(start=round(a, 2), end=round(b, 2), duration=round(b - a, 2), adjusted_by_llm=True)
        w["score"] = round(0.6 * grade + 0.4 * w["formula_score"], 3)
        out.append(w)
    return out


# ------------------------------------------------------------------ run
def run(episodes: dict[int, Episode], out: Path, cfg: dict, llm=None, aliases_by_ep=None, progress=None) -> dict:
    p = cfg["pipeline"]
    say = progress or (lambda *a, **k: None)
    grades, failed = {}, []

    use_model = cfg.get("grader") in (None, "auto", "model") and model_available()
    grader_name = "trained model" if use_model else ("quick grader" if llm is None else "AI grader")

    def grade(n):
        if use_model:
            return model_grades(episodes[n])
        if llm is None:
            return keyword_grades(episodes[n])
        return llm_grades(llm, episodes[n], (aliases_by_ep or {}).get(str(n)))

    with ThreadPoolExecutor(max_workers=int(cfg["llm"].get("max_workers", 4)) if (llm and not use_model) else 1) as ex:
        futs = {ex.submit(grade, n): n for n in sorted(episodes)}
        for k, f in enumerate(as_completed(futs), 1):
            n = futs[f]
            try:
                grades[n] = f.result()
            except Exception as e:
                if type(e).__name__ == "PendingAnswer":
                    raise
                failed.append(n)
                grades[n] = keyword_grades(episodes[n])       # never leave an episode ungraded
                say(f"episode {n}: model grading failed ({type(e).__name__}); fallback grades used", None)
            say(f"graded episode {n}", 0.1 + 0.7 * k / len(futs))
    allw, per_ep, final_failed, n_scored = [], {}, [], 0
    for k_, (n, ep) in enumerate(sorted(episodes.items()), 1):
        ws = score_windows(ep, heat_curve(ep, grades[n]), p)
        n_scored += len(ws)
        shortlist = pick(ws, int(p.get("final_candidates", 6)), None, 0.5)
        if llm is not None:
            try:
                shortlist = finalize(llm, ep, shortlist, p)
                say(f"finalising episode {n}", 0.8 + 0.15 * k_ / len(episodes))
            except Exception as e:
                if type(e).__name__ == "PendingAnswer":
                    raise
                final_failed.append(n)
        top3 = pick(shortlist, 3, None)
        for ov in (0.5, 0.75, 0.9):                         # short episode: allow growing overlap
            if len(top3) >= 3:
                break
            top3 = pick(shortlist + ws, 3, None, ov)
        allw += shortlist
        per_ep[n] = [describe(ep, w, grades[n]) for w in top3]
    k = int(p.get("top_windows", 3))
    top = [describe(episodes[w["episode"]], w, grades[w["episode"]]) for w in pick(allw, k, p.get("top_max_per_episode", 1))]
    for i, w in enumerate(top, 1):
        w["rank"] = i
    res = {"grader": grader_name, "final_assessment": None if llm is None else "AI",
           "episodes": len(episodes), "windows_scored": n_scored, "grading_failed_episodes": failed,
           "final_assessment_failed_episodes": final_failed, "top": top,
           "per_episode": {str(n): v for n, v in sorted(per_ep.items())}}
    out.mkdir(parents=True, exist_ok=True)
    (out / "sensational.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    (out / "block_grades.json").write_text(json.dumps({str(n): g for n, g in sorted(grades.items())}, indent=1),
                                           encoding="utf-8")
    with open(out / "sensational_top.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["rank", "episode", "start", "end", "duration_s", "score", "mean_heat", "peak", "hook", "ending",
                    "tags", "opens_on", "ends_on"])
        for x in top:
            w.writerow([x["rank"], x["episode"], x["start_ts"], x["end_ts"], x["duration"], x["score"], x["mean_heat"],
                        x["peak"], x["hook"], x["ending"], "; ".join(x["tags"]), x["opens_on"], x["ends_on"]])
    with open(out / "sensational_per_episode.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["episode", "rank_in_episode", "start", "end", "duration_s", "score", "tags", "opens_on", "ends_on"])
        for n, v in sorted(per_ep.items()):
            for i, x in enumerate(v, 1):
                w.writerow([n, i, x["start_ts"], x["end_ts"], x["duration"], x["score"], "; ".join(x["tags"]),
                            x["opens_on"], x["ends_on"]])
    (out / "sensational.md").write_text(render(res, cfg.get("title", "")), encoding="utf-8")
    return res


def render(res: dict, title: str) -> str:
    L = [f"# Top {len(res['top'])} sensational windows{' — ' + title if title else ''}", "",
         f"Grader: {res['grader']}. {res['episodes']} episodes, {res['windows_scored']:,} candidate windows of 20-60 s.",
         "Times are MM:SS.ff from the start of the episode.", "",
         "| # | Episode | Start | End | Duration | Score | Heated moments |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for w in res["top"]:
        L.append(f"| {w['rank']} | {w['episode']} | {w['start_ts']} | {w['end_ts']} | {w['duration']:.1f} s | "
                 f"{w['score']:.2f} | {', '.join(w['tags']) or '-'} |")
    for w in res["top"]:
        L += ["", f"## {w['rank']}. Episode {w['episode']}, {w['start_ts']}-{w['end_ts']} ({w['duration']:.1f} s)", "",
              f"- Opens on: {w['opens_on']}", f"- Ends on: {w['ends_on']}",
              f"- Heat: mean {w['mean_heat']}/10, peak {w['peak']}/10, first 3 s {w['hook']}/10, last 5 s {w['ending']}/10"]
        if w["dimensions"]:
            L.append("- Strongest: " + ", ".join(f"{k} {v}/3" for k, v in sorted(w["dimensions"].items(), key=lambda x: -x[1])))
        if w.get("llm_grade") is not None:
            L.append(f"- AI grade as a whole clip: {w['llm_grade']}/10. {w.get('llm_why', '')}")
        for h in w["hottest_moments"]:
            L.append(f"- Hottest at {h['at']} ({h['score']}/10): {h['text']}")
    return "\n".join(L) + "\n"
