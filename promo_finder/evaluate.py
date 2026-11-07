"""Stage 7: how good is the run, and where does it pick badly?

Always computed: the funnel, rejection reasons, model self-corrections, judge
agreement between samples, bias checks, failure examples.
Computed when present: your reference labels (reference.json), your app
reviews (reviews.json) and a baseline run to compare against."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .schemas import DIMENSIONS, RankedPromo


def load_ranked(run_dir: Path) -> list[RankedPromo]:
    return [RankedPromo.model_validate(r) for r in json.loads((run_dir / "promos_ranked.json").read_text(encoding="utf-8"))]


def _block_times(run_dir: Path) -> dict:
    out = {}
    for f in sorted((run_dir / "parsed").glob("episode-*.json")):
        ep = json.loads(f.read_text(encoding="utf-8"))
        for s in ep["scenes"]:
            for b in s["blocks"]:
                out[b["block_id"]] = {"start": b["start"], "end": b["end"], "episode": ep["episode"]}
    return out


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 3:
        return None
    ra, rb = _ranks(a), _ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va, vb = sum((x - ma) ** 2 for x in ra), sum((y - mb) ** 2 for y in rb)
    return round(cov / (va * vb) ** 0.5, 3) if va and vb else None


def _overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def reference_metrics(rows: list[RankedPromo], ref: dict, blocks: dict, k: int = 3, tol: float = 3.0) -> dict:
    """Compare a run with an answer key (reference.json).

    pool    = candidates that passed every gate (shipped or left out only by the list size / episode cap)
    hit@k   = one of the episode's top-k pool candidates by score has its hook (or cliffhanger) block within
              tol seconds of the key's; "lenient" also accepts the key's alternatives
    found   = a must-use promo is covered at least half by some pool candidate
    in list = ... by a promo in the final ranked list (bounded by list size)
    Each rate is averaged only over episodes in this run that carry that label."""
    in_run = {b["episode"] for b in blocks.values()}
    pool = sorted([r for r in rows if r.status in ("shipped", "not_selected") and r.composite is not None],
                  key=lambda r: -r.composite)
    shipped = [r for r in rows if r.status == "shipped"]
    acc = {"hook": [0, 0, 0], "cliffhanger": [0, 0, 0]}      # labels, strict hits, lenient hits
    must = {"total": 0, "found": 0, "in_list": 0}
    per_ep, skipped, strength, best_score = {}, [], [], []

    def near(bid, keys, field):
        b = blocks.get(bid or "")
        return bool(b) and any(abs(b[field] - blocks[x][field]) <= tol for x in keys if x in blocks)

    def covers(rs, n, a, b):
        return any(s.episode == n and _overlap(a, b, s.start, s.end) >= 0.5 * (b - a)
                   for r in rs for s in r.candidate.segments)

    for n_str, lab in (ref.get("episodes") or {}).items():
        n = int(n_str)
        if n not in in_run:
            skipped.append(n)
            continue
        mine = [r for r in pool if r.candidate.segments[0].episode == n]
        top = mine[:k]
        rec = {"pool_candidates": len(mine)}
        for kind, field in (("hook", "start"), ("cliffhanger", "end")):
            best = lab.get(kind)
            if best not in blocks:
                continue
            alts = [x for x in lab.get(f"alt_{kind}s", []) if x in blocks]
            got = [getattr(r.candidate, f"{kind}_block") for r in top]
            strict = any(near(g, [best], field) for g in got)
            lenient = any(near(g, [best] + alts, field) for g in got)
            acc[kind][0] += 1
            acc[kind][1] += strict
            acc[kind][2] += lenient
            rec[f"{kind}_hit"], rec[f"{kind}_hit_lenient"] = strict, lenient
        for a, b in lab.get("must_promo", []):
            must["total"] += 1
            f, l = covers(pool, n, a, b), covers(shipped, n, a, b)
            must["found"] += f
            must["in_list"] += l
            rec.setdefault("must_found", []).append(f)
        if isinstance(lab.get("promo_strength"), int):
            rec["strength"] = lab["promo_strength"]
            strength.append(lab["promo_strength"])
            best_score.append(mine[0].composite if mine else 0.0)
        per_ep[n] = rec

    def rate(x, y):
        return round(x / y, 2) if y else None

    strong = {n for n, r in per_ep.items() if r.get("strength", 0) >= 4}
    ship_eps = [r.candidate.segments[0].episode for r in shipped if r.candidate.segments[0].episode in per_ep]
    slate_eps = [r.candidate.segments[0].episode for r in shipped if r.slate and r.candidate.segments[0].episode in per_ep]
    mean = lambda xs: round(sum(xs) / len(xs), 2) if xs else None
    return {
        "episodes_labelled": len(per_ep), "labels_skipped_not_in_run": skipped, "k": k, "tolerance_s": tol,
        f"hook_hit@{k}": rate(acc["hook"][1], acc["hook"][0]),
        f"hook_hit@{k}_lenient": rate(acc["hook"][2], acc["hook"][0]),
        f"cliffhanger_hit@{k}": rate(acc["cliffhanger"][1], acc["cliffhanger"][0]),
        f"cliffhanger_hit@{k}_lenient": rate(acc["cliffhanger"][2], acc["cliffhanger"][0]),
        "must_promos": must["total"],
        "must_promo_found": rate(must["found"], must["total"]),
        "must_promo_in_ranked_list": rate(must["in_list"], must["total"]),
        "ranked_from_strong_episodes": rate(sum(e in strong for e in ship_eps), len(ship_eps)),
        "strong_episode_base_rate": rate(len(strong), len(per_ep)),
        "slate_mean_strength": mean([per_ep[e]["strength"] for e in slate_eps if "strength" in per_ep[e]]),
        "mean_strength_all_episodes": mean(strength),
        "spearman_strength_vs_best_score": spearman([float(x) for x in strength], best_score),
        "per_episode": per_ep,
    }


def review_metrics(rows: list[RankedPromo], reviews: dict, k: int = 10) -> dict:
    top = [r for r in rows if r.status == "shipped"][:k]
    seen = [r for r in top if r.promo_id in reviews]
    would = [r for r in seen if reviews[r.promo_id].get("decision") == "approve"]
    reasons = Counter(v.get("reason") for v in reviews.values() if v.get("decision") == "reject" and v.get("reason"))
    # blind controls: candidates that did not ship but were shown to the reviewer
    ctrl = [pid for pid, v in reviews.items() if v.get("control")]
    ctrl_ok = [pid for pid in ctrl if reviews[pid].get("decision") == "approve"]
    return {f"precision@{k}": round(len(would) / len(seen), 2) if seen else None,
            "reviewed_in_top": len(seen),
            "control_approval_rate": round(len(ctrl_ok) / len(ctrl), 2) if ctrl else None,
            "reject_reasons": dict(reasons)}


def evaluate(run_dir: str | Path, reference: str | Path | None = None, reviews: str | Path | None = None,
             baseline_dir: str | Path | None = None) -> dict:
    run_dir = Path(run_dir)
    rows = load_ranked(run_dir)
    cands = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
    calls = [json.loads(l) for l in (run_dir / "llm_calls.jsonl").read_text(encoding="utf-8").splitlines()] \
        if (run_dir / "llm_calls.jsonl").exists() else []
    blocks = _block_times(run_dir)
    status = Counter(r.status for r in rows)
    shipped = [r for r in rows if r.status == "shipped"]
    judged = [r for r in rows if r.judgement]

    ev = {
        "funnel": {"proposed": cands["proposed"], "build_failures": len(cands["build_failures"]),
                   "built": len(rows), "valid": sum(r.validation.passed for r in rows),
                   "passed_gates": status["shipped"] + status["not_selected"] + status["duplicate"],
                   "duplicates_removed": status["duplicate"], "shipped": status["shipped"]},
        "build_failure_reasons": dict(Counter(f["reason"].split(":")[0] for f in cands["build_failures"])),
        "validation_failures_by_rule": dict(Counter(c.rule for r in rows for c in r.validation.failures)),
        "model_calls": {"total": len(calls), "needed_a_correction": sum(c["rounds"] > 1 for c in calls),
                        "still_wrong_after_correction": sum(c["unfixed_problems"] > 0 for c in calls)},
        "judge": {
            "mean_scores_shipped": {k: round(sum(r.judgement.scores[k] for r in shipped) / len(shipped), 2)
                                    for k in DIMENSIONS} if shipped else {},
            "mean_spread_between_samples": {k: round(sum(r.judgement.spread[k] for r in judged) / len(judged), 2)
                                            for k in DIMENSIONS} if judged else {},
            "share_with_spread_2plus": round(sum(any(v >= 2 for v in r.judgement.spread.values()) for r in judged)
                                             / len(judged), 2) if judged else None,
            "evidence_outside_clip": sum(not r.judgement.evidence_ok for r in judged),
        },
        "bias": {
            "shipped_ending_in_final_10s": sum(any(w.rule == "W-ending" for w in r.validation.warnings) for r in shipped),
            "shipped_opening_on_recap": sum(any(w.rule == "W-recap" for w in r.validation.warnings) for r in shipped),
            "shipped_multi_segment": sum(len(r.candidate.segments) > 1 for r in shipped),
            "shipped_cross_episode": sum(len(r.candidate.episodes) > 1 for r in shipped),
            "episodes_covered_by_shipped": len({e for r in shipped for e in r.candidate.episodes}),
            "shipped_with_review_flags": sum(bool(r.review_flags) for r in shipped),
        },
        "review_flags_shipped": dict(Counter(f.split(":")[0] for r in shipped for f in r.review_flags)),
    }
    if reference and Path(reference).exists():
        ev["reference"] = reference_metrics(rows, json.loads(Path(reference).read_text(encoding="utf-8")), blocks)
    if reviews and Path(reviews).exists():
        ev["reviews"] = review_metrics(rows, json.loads(Path(reviews).read_text(encoding="utf-8")))
    if baseline_dir and (Path(baseline_dir) / "promos_ranked.json").exists():
        base = load_ranked(Path(baseline_dir))
        b_ship = [r for r in base if r.status == "shipped"]
        overlap = sum(any(sa.episode == sb.episode and min(sa.end, sb.end) - max(sa.start, sb.start) > 5
                          for sa in r.candidate.segments for x in b_ship for sb in x.candidate.segments)
                      for r in shipped)
        ev["baseline"] = {"baseline_shipped": len(b_ship), "llm_shipped_overlapping_baseline": overlap}
        if reference and Path(reference).exists():
            ev["baseline"]["reference"] = reference_metrics(base, json.loads(Path(reference).read_text(encoding="utf-8")), blocks)
    ev["failure_examples"] = failure_examples(rows, cands)
    (run_dir / "eval.json").write_text(json.dumps(ev, indent=2), encoding="utf-8")
    (run_dir / "eval_report.md").write_text(render_report(ev), encoding="utf-8")
    return ev


def failure_examples(rows: list[RankedPromo], cands: dict, per_kind: int = 3) -> list[dict]:
    out = []
    for f in cands["build_failures"][:per_kind]:
        out.append({"kind": "model proposal could not be built", "promo_id": f["promo_id"], "detail": f["reason"]})
    inval = [r for r in rows if r.status == "invalid"]
    for r in inval[:per_kind]:
        out.append({"kind": "failed validation", "promo_id": r.promo_id,
                    "detail": "; ".join(c.detail for c in r.validation.failures)})
    low = sorted([r for r in rows if r.status == "below_gate" and r.judgement], key=lambda r: r.composite or 0)
    for r in low[:per_kind]:
        reason = r.judgement.samples[0].reasons
        out.append({"kind": "picked by the miner, failed the judge's gate", "promo_id": r.promo_id,
                    "detail": f"{r.candidate.title}: " + "; ".join(f for f in r.review_flags[:3]) +
                              (f" | judge on cliffhanger: {reason.get('cliffhanger', '')}" if reason else "")})
    flagged = [r for r in rows if r.status == "shipped" and r.review_flags]
    for r in flagged[:per_kind]:
        out.append({"kind": "shipped but flagged for review", "promo_id": r.promo_id,
                    "detail": f"rank {r.rank}: " + "; ".join(r.review_flags[:4])})
    dups = [r for r in rows if r.status == "duplicate"]
    for r in dups[:2]:
        out.append({"kind": "duplicate story beat removed", "promo_id": r.promo_id,
                    "detail": f"same story as {r.duplicate_of}: {r.candidate.beat}"})
    return out


def render_report(ev: dict) -> str:
    L = ["# Evaluation report", ""]
    f = ev["funnel"]
    L += ["## Funnel", "",
          f"{f['proposed']} proposed -> {f['built']} built ({f['build_failures']} could not be built) -> "
          f"{f['valid']} valid -> {f['passed_gates']} passed the judge's gates -> "
          f"{f['duplicates_removed']} duplicates removed -> {f['shipped']} shipped.", ""]
    L += ["## Why candidates were rejected", ""]
    for k, v in sorted(ev["build_failure_reasons"].items(), key=lambda x: -x[1]):
        L.append(f"- build: {k}: {v}")
    for k, v in sorted(ev["validation_failures_by_rule"].items()):
        L.append(f"- validation {k}: {v}")
    mc = ev["model_calls"]
    L += ["", "## Model self-correction", "",
          f"{mc['needed_a_correction']} of {mc['total']} model calls needed a correction round; "
          f"{mc['still_wrong_after_correction']} were still wrong afterwards (code then rejected or repaired them).", ""]
    j = ev["judge"]
    if j["mean_scores_shipped"]:
        L += ["## Judge", "", "| Dimension | Mean score, shipped | Mean gap between judge samples |", "| --- | --- | --- |"]
        for k in DIMENSIONS:
            L.append(f"| {k} | {j['mean_scores_shipped'].get(k, '-')} | {j['mean_spread_between_samples'].get(k, '-')} |")
        L += ["", f"Share of judged clips where samples differ by 2+ on some dimension: {j['share_with_spread_2plus']}. "
                  f"Clips where the judge cited blocks outside the clip: {j['evidence_outside_clip']}.", ""]
    b = ev["bias"]
    L += ["## Bias checks (shipped promos)", "",
          f"- end in an episode's final 10 s: {b['shipped_ending_in_final_10s']}",
          f"- open on a recap of the previous episode: {b['shipped_opening_on_recap']}",
          f"- multi-segment: {b['shipped_multi_segment']}; cross-episode: {b['shipped_cross_episode']}",
          f"- episodes represented: {b['episodes_covered_by_shipped']}",
          f"- carry a review flag: {b['shipped_with_review_flags']}", ""]
    if ev.get("reference"):
        r = ev["reference"]
        L += ["## Against the answer key", "",
              f"{r['episodes_labelled']} episodes labelled; top {r['k']} candidates per episode; "
              f"a hit is within {r['tolerance_s']:.0f} s of the key.", "",
              "| Measure | Strict | Lenient (key's alternatives count) |", "| --- | --- | --- |",
              f"| Hook hit@{r['k']} | {r.get('hook_hit@3')} | {r.get('hook_hit@3_lenient')} |",
              f"| Cliffhanger hit@{r['k']} | {r.get('cliffhanger_hit@3')} | {r.get('cliffhanger_hit@3_lenient')} |", "",
              f"- Must-use promos found by some candidate that passed the gates: {r.get('must_promo_found')} "
              f"of {r['must_promos']}",
              f"- ... and kept in the final ranked list: {r.get('must_promo_in_ranked_list')} (the list holds at most "
              f"one promo per handful of episodes, so this is capped well below 1)",
              f"- Ranked promos from episodes the key rates 4-5: {r.get('ranked_from_strong_episodes')} "
              f"(base rate {r.get('strong_episode_base_rate')})",
              f"- Mean key strength of the slate's episodes: {r.get('slate_mean_strength')} "
              f"(all episodes {r.get('mean_strength_all_episodes')})",
              f"- Rank correlation, key strength vs the episode's best score: {r.get('spearman_strength_vs_best_score')}",
              ""]
    else:
        L += ["## Against the answer key", "", "No reference.json given.", ""]
    if ev.get("reviews"):
        r = ev["reviews"]
        L += ["## Your reviews", "", f"Precision@10: {r.get('precision@10')} ({r['reviewed_in_top']} reviewed). "
              f"Approval rate of blind controls: {r.get('control_approval_rate')}. Reject reasons: {r['reject_reasons']}.", ""]
    if ev.get("baseline"):
        r = ev["baseline"]
        L += ["## Against the no-LLM baseline", "", f"Baseline shipped {r['baseline_shipped']}; "
              f"{r['llm_shipped_overlapping_baseline']} LLM promos overlap a baseline promo."]
        if r.get("reference") and ev.get("reference"):
            a, b = ev["reference"], r["reference"]
            L += ["", "| Measure | This run | Baseline |", "| --- | --- | --- |"]
            for key in ("hook_hit@3", "hook_hit@3_lenient", "cliffhanger_hit@3", "cliffhanger_hit@3_lenient",
                        "must_promo_found", "ranked_from_strong_episodes", "slate_mean_strength",
                        "spearman_strength_vs_best_score"):
                L.append(f"| {key} | {a.get(key)} | {b.get(key)} |")
        L.append("")
    L += ["## Where it picks badly (examples)", ""]
    for e in ev["failure_examples"]:
        L.append(f"- **{e['kind']}** ({e['promo_id']}): {e['detail']}")
    return "\n".join(L) + "\n"
