"""Run Promo Finder on the ReelBench synthetic pack and score it against the pack's ground truth.

  python eval/reelbench.py run   --bench <reelbench dir> --out bench/baseline --mode baseline
  python eval/reelbench.py run   --bench <reelbench dir> --out bench/ai --per-series 8          # AI, 8 eps per show
  python eval/reelbench.py score --bench <reelbench dir> --runs bench/baseline

Each show is run as its own series (its own character bible). A promo "matches" a ground-truth (GT) window
by IoU = overlap / union of their time spans within the episode. Two controls are scored the same way:
  - fixed-time guess: the same two windows in every episode, read off the pack's layout (no text used)
  - random cuts: random clean-cut 20-60 s windows, as many per episode as the run's gated pool
"""
from __future__ import annotations

import argparse
import json
import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from promo_finder.cuts import windows  # noqa: E402
from promo_finder.evaluate import load_ranked  # noqa: E402
from promo_finder.parse import load_parsed  # noqa: E402
from promo_finder.validate import Context  # noqa: E402


def load_gt(bench: Path) -> dict:
    gt = json.loads((bench / "annotations" / "promo_ground_truth.json").read_text(encoding="utf-8"))
    out = defaultdict(lambda: {"title": "", "episodes": {}})
    for a in gt["annotations"]:
        slug = Path(a["scriptFile"]).parent.name
        out[slug]["title"] = a["seriesTitle"]
        out[slug]["episodes"][a["episodeNumber"]] = a["promoMoments"]
    return dict(out)


# ------------------------------------------------------------------ run
def cmd_run(args):
    from promo_finder.config import load_config
    from promo_finder.pipeline import run
    bench, out = Path(args.bench), Path(args.out)
    gt = load_gt(bench)
    cfg = load_config(None, {"llm": {"provider": args.provider, "model": args.model}})
    if args.per_series:
        cfg["episode_filter"] = f"1-{args.per_series}"
    from promo_finder.pipeline import PendingStop
    waiting = []
    for slug in sorted(gt):
        print(f"=== {slug}", flush=True)
        c = json.loads(json.dumps(cfg))
        c["title"] = gt[slug]["title"]
        for k in ("judge_samples", "promos_per_episode", "montage_promos"):
            if getattr(args, k, None) is not None:
                c["pipeline"][k] = getattr(args, k)
        try:
            run(bench / "scripts" / slug, out / slug, c, mode=args.mode,
                progress=lambda m, f=None: print("  " + m, flush=True) if not m.startswith(("judged", "mined")) else None)
        except PendingStop as e:
            waiting.append(slug)
            print(f"  waiting: {e}", flush=True)
    if waiting:
        raise SystemExit(f"WAITING on {len(waiting)} show(s): answer the pending prompts, then run again.")


# ------------------------------------------------------------------ score
def iou_in_episode(segs, ep: int, a: float, b: float) -> float:
    spans = [(s.start, s.end) for s in segs if s.episode == ep]
    if not spans:
        return 0.0
    inter = sum(max(0.0, min(e, b) - max(s, a)) for s, e in spans)
    union = sum(e - s for s, e in spans) + (b - a) - inter
    return inter / union if union > 0 else 0.0


def score_series(run_dir: Path, gt_eps: dict, seed: int = 0) -> dict:
    rows = load_ranked(run_dir)
    eps = load_parsed(run_dir / "parsed")
    ctx = Context(eps)
    pool = [r for r in rows if r.status in ("shipped", "not_selected") and r.composite is not None]
    valid = [r for r in rows if r.validation.passed]
    shipped = [r for r in rows if r.status == "shipped"]
    by_ep = defaultdict(list)
    for r in sorted(pool, key=lambda r: -r.composite):
        by_ep[r.candidate.segments[0].episode].append(r)
    # fixed-time guess: the most common GT windows in this series (uses no text at all)
    from collections import Counter
    fixed = [w for w, _ in Counter((m["startSec"], m["endSec"]) for ms in gt_eps.values() for m in ms).most_common(2)]
    rnd = random.Random(seed)
    recs = []
    for n, moments in gt_eps.items():
        if n not in eps:
            continue
        top3 = by_ep.get(n, [])[:3]
        wins = list(windows(ctx.cuts[n]))
        rand = [rnd.choice(wins) for _ in range(max(1, len(by_ep.get(n, []))))] if wins else []
        for m in moments:
            a, b = m["startSec"], m["endSec"]

            def best(rs):
                vals = [(iou_in_episode(r.candidate.segments, n, a, b), r) for r in rs if n in r.candidate.episodes]
                return max(vals, key=lambda x: x[0]) if vals else (0.0, None)
            i_valid, _ = best(valid)
            i_pool, r_pool = best(pool)
            i_top3, _ = best(top3)
            i_ship, _ = best([r for r in shipped if n in r.candidate.episodes])
            i_fixed = max((max(0, min(e, b) - max(s, a)) / (max(e, b) - min(s, a)) for s, e in fixed), default=0)
            i_rand = max(((max(0, min(e, b) - max(s, a)) / ((e - s) + (b - a) - max(0, min(e, b) - max(s, a))))
                          for s, e in rand), default=0)
            rec = {"episode": n, "gt_id": m["id"], "category": m["hookCategory"], "gt": [a, b],
                   "iou_valid": i_valid, "iou_pool": i_pool, "iou_top3": i_top3, "iou_ranked": i_ship,
                   "iou_fixed_guess": i_fixed, "iou_random": i_rand}
            if r_pool:
                seg = [s for s in r_pool.candidate.segments if s.episode == n]
                rec["best_pool_cut"] = [round(seg[0].start, 2), round(seg[-1].end, 2)]
                rec["start_offset_s"] = round(seg[0].start - a, 2)
                rec["end_offset_s"] = round(seg[-1].end - b, 2)
                rec["best_pool_promo"] = r_pool.promo_id
            recs.append(rec)
    return {"records": recs, "pool": len(pool), "valid": len(valid), "shipped": len(shipped),
            "episodes": len([n for n in gt_eps if n in eps])}


def summarise(recs: list[dict]) -> dict:
    def share(key, thr):
        return round(sum(r[key] >= thr for r in recs) / len(recs), 2) if recs else None
    out = {"gt_windows": len(recs)}
    for key in ("iou_valid", "iou_pool", "iou_top3", "iou_ranked", "iou_fixed_guess", "iou_random"):
        out[key] = {"mean": round(st.mean(r[key] for r in recs), 3) if recs else None,
                    "recall@0.5": share(key, 0.5), "recall@0.7": share(key, 0.7)}
    offs = [r for r in recs if "start_offset_s" in r and r["iou_pool"] >= 0.5]
    if offs:
        out["matched_start_offset_median_s"] = st.median(r["start_offset_s"] for r in offs)
        out["matched_end_offset_median_s"] = st.median(r["end_offset_s"] for r in offs)
    return out


def cmd_score(args):
    bench, runs = Path(args.bench), Path(args.runs)
    gt = load_gt(bench)
    allrecs, per_series, meta = [], {}, {}
    for slug in sorted(gt):
        d = runs / slug
        if not (d / "promos_ranked.json").exists():
            continue
        res = score_series(d, gt[slug]["episodes"])
        for r in res["records"]:
            r["series"] = slug
        allrecs += res["records"]
        per_series[slug] = summarise(res["records"])
        meta[slug] = {k: res[k] for k in ("episodes", "valid", "pool", "shipped")}
    by_cat = defaultdict(list)
    by_slot = defaultdict(list)
    for r in allrecs:
        by_cat[r["category"]].append(r)
        by_slot["window 1 (mid-episode)" if r["gt"][0] < 60 else "window 2 (end of episode)"].append(r)
    report = {"overall": summarise(allrecs), "per_series": per_series, "meta": meta,
              "by_category": {k: summarise(v) for k, v in sorted(by_cat.items())},
              "by_slot": {k: summarise(v) for k, v in sorted(by_slot.items())},
              "misses": [r for r in allrecs if r["iou_pool"] < 0.5]}
    (runs / "reelbench_score.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (runs / "reelbench_report.md").write_text(render(report), encoding="utf-8")
    print(render(report))


def render(rep: dict) -> str:
    o = rep["overall"]
    L = ["# ReelBench score", "",
         "IoU = overlap / union of a promo and a ground-truth window, within the episode. Recall@0.5 = share of the",
         "ground-truth windows matched at IoU 0.5 or better.", "",
         "| Candidates considered | Mean IoU | Recall@0.5 | Recall@0.7 |", "| --- | --- | --- | --- |"]
    names = {"iou_valid": "Any valid candidate", "iou_pool": "Candidates that passed the judge's gates",
             "iou_top3": "Top 3 per episode by score", "iou_ranked": "Final ranked list",
             "iou_fixed_guess": "Control: fixed-time guess (no text)", "iou_random": "Control: random clean cuts"}
    for k, label in names.items():
        v = o[k]
        L.append(f"| {label} | {v['mean']} | {v['recall@0.5']} | {v['recall@0.7']} |")
    if "matched_start_offset_median_s" in o:
        L += ["", f"Matched cuts start a median {o['matched_start_offset_median_s']:+.1f} s and end "
                  f"{o['matched_end_offset_median_s']:+.1f} s from the ground truth."]
    for title, block in (("By window", rep["by_slot"]), ("By hook category", rep["by_category"]),
                         ("By series", rep["per_series"])):
        L += ["", f"## {title}", "", "| Group | GT windows | Top-3 recall@0.5 | Gated-pool recall@0.5 | Ranked-list recall@0.5 |",
              "| --- | --- | --- | --- | --- |"]
        for k, v in block.items():
            L.append(f"| {k} | {v['gt_windows']} | {v['iou_top3']['recall@0.5']} | {v['iou_pool']['recall@0.5']} | "
                     f"{v['iou_ranked']['recall@0.5']} |")
    L += ["", f"{len(rep['misses'])} ground-truth windows had no gated candidate at IoU 0.5 or better.", ""]
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--bench", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--mode", choices=["llm", "baseline"], default="llm")
    r.add_argument("--provider", default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--per-series", type=int, default=None, help="only the first N episodes of each show")
    r.add_argument("--judge-samples", dest="judge_samples", type=int, default=None)
    r.add_argument("--promos-per-episode", dest="promos_per_episode", type=int, default=None)
    r.add_argument("--montage-promos", dest="montage_promos", type=int, default=None)
    s = sub.add_parser("score")
    s.add_argument("--bench", required=True)
    s.add_argument("--runs", required=True)
    a = ap.parse_args()
    cmd_run(a) if a.cmd == "run" else cmd_score(a)
