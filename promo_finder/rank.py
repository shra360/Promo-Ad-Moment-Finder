"""Stage 6: rank. Gates first (validity, then hook/cliffhanger/standalone >= 3),
then a weighted score, then dedupe by story beat, a per-episode cap, and a
publish-first slate. Every candidate keeps a status and a reason."""
from __future__ import annotations

import re

from .render import clip_blocks
from .schemas import Candidate, Judgement, RankedPromo, Validation

STOP = {"the", "a", "an", "of", "to", "and", "in", "at", "her", "his", "for", "on", "with", "is", "by", "as"}


def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (t or "").lower()) if w not in STOP and len(w) > 2}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def composite(j: Judgement, weights: dict) -> float:
    return round(sum(w * j.scores.get(k, 0) for k, w in weights.items()) / sum(weights.values()), 3)


def _same_story(a: Candidate, b: Candidate, episodes, thr: float) -> bool:
    if _jaccard(_words(a.beat), _words(b.beat)) >= thr:
        return True
    shared = sum(max(0.0, min(sa.end, sb.end) - max(sa.start, sb.start))   # overlapping footage
                 for sa in a.segments for sb in b.segments if sa.episode == sb.episode)
    if shared > 0.5 * min(a.total, b.total):
        return True
    ea, eb = set(a.episodes), set(b.episodes)   # adjacent episodes re-staging the same lines
    if any(abs(x - y) == 1 for x in ea for y in eb):
        ta = _words(" ".join(bl.text for s in a.segments for bl in clip_blocks(episodes[s.episode], s.start, s.end)
                             if bl.kind == "dialogue"))
        tb = _words(" ".join(bl.text for s in b.segments for bl in clip_blocks(episodes[s.episode], s.start, s.end)
                             if bl.kind == "dialogue"))
        if _jaccard(ta, tb) >= 0.5:
            return True
    return False


def rank(cands: list[Candidate], vals: dict[str, Validation], judgements: dict[str, Judgement | None],
         episodes, cfg: dict) -> list[RankedPromo]:
    rc = cfg["ranking"]
    weights, gates = rc["weights"], rc["gates"]
    rows: list[RankedPromo] = []
    pool = []
    for c in cands:
        v, j = vals[c.promo_id], judgements.get(c.promo_id)
        flags = []
        if not v.passed:
            rows.append(RankedPromo(rank=None, promo_id=c.promo_id, status="invalid", composite=None,
                                    candidate=c, validation=v, judgement=j))
            continue
        if j is None:
            rows.append(RankedPromo(rank=None, promo_id=c.promo_id, status="below_gate", composite=None,
                                    candidate=c, validation=v, judgement=None, review_flags=["judge failed"]))
            continue
        score = composite(j, weights)
        failed = [k for k, g in gates.items() if j.scores.get(k, 0) < g]
        flags += [f"{k} below {gates[k]}" for k in failed]
        flags += j.notes
        if not j.evidence_ok:
            flags.append("judge cited blocks outside the clip")
        flags += [f"judges disagree on {k} by {d:.0f}" for k, d in j.spread.items() if d >= 2]
        safety = sorted({f for s in j.samples for f in s.safety_flags})
        if safety:
            flags.append("ad-safety: " + ", ".join(safety))
        flags += [w.detail for w in v.warnings if w.rule in ("W-safety", "W-names", "W-recap")]
        r = RankedPromo(rank=None, promo_id=c.promo_id, status="below_gate" if failed else "shipped",
                        composite=score, candidate=c, validation=v, judgement=j, review_flags=flags)
        rows.append(r)
        if not failed:
            pool.append(r)

    pool.sort(key=lambda r: (-r.composite, -r.judgement.scores.get("cliffhanger", 0), r.candidate.total))
    kept: list[RankedPromo] = []
    per_ep: dict[int, int] = {}
    for r in pool:
        dup = next((k for k in kept if _same_story(r.candidate, k.candidate, episodes, rc["dedupe_similarity"])), None)
        if dup:
            r.status, r.duplicate_of = "duplicate", dup.promo_id
            continue
        if any(per_ep.get(e, 0) >= rc["max_per_episode"] for e in r.candidate.episodes) or len(kept) >= rc["final_count"]:
            r.status = "not_selected"
            r.review_flags.append("passed the gates; left out by the episode cap or list size")
            continue
        kept.append(r)
        for e in r.candidate.episodes:
            per_ep[e] = per_ep.get(e, 0) + 1
    for i, r in enumerate(kept, 1):
        r.rank = i
        r.slate = i <= rc["slate_size"]
    order = {"shipped": 0, "not_selected": 1, "below_gate": 2, "duplicate": 3, "invalid": 4}
    rows.sort(key=lambda r: (order[r.status], r.rank or 10**6, -(r.composite or 0)))
    return rows
