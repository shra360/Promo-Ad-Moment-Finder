"""Stage 4: deterministic validation (rules V1-V11) and warnings.
Nothing here trusts the model: every number is recomputed from the parsed source."""
from __future__ import annotations

import re

from .cuts import clean_cut_points
from .parse import Episode
from .schemas import Candidate, Check, Validation

EPS = 0.05

SAFETY_TERMS = {
    "sexual": ["naked", "bare chest", "bare-chested", "shirtless", "chemise", "carnal", "undress",
               "post-coital", "bedsheet", "sheets pulled", "lovemaking", "intimacy"],
    "gore": ["blood", "bleeding", "gash", "wound", "lacerat", "corpse", "severed"],
    "violence": ["hang ", "hanged", "noose", "gallows", "stab", "strangle", "kill", "murder", "slap",
                 "strikes", "explosion", "gun", "sword"],
    "self-harm": ["suicide", "poison herself", "end my life"],
}


class Context:
    """Per-run lookups shared by assembly and validation."""

    def __init__(self, episodes: dict[int, Episode]):
        self.episodes = episodes
        # blocks without readable times can never be cut on, so they are not addressable
        self.blocks = {bid: b for ep in episodes.values() for bid, b in ep.block_map().items()
                       if b.start is not None and b.end is not None}
        self.cuts = {n: clean_cut_points(ep) for n, ep in episodes.items()}
        self.lines = {n: [(b.start, b.end) for b in ep.blocks() if b.kind == "dialogue" and b.start is not None]
                      for n, ep in episodes.items()}

    def block_episode(self, bid: str) -> int | None:
        m = re.match(r"E(\d+)-S\d+-B\d+$", bid or "")
        return int(m.group(1)) if m else None


def _on_cut(t: float, cuts: list[float]) -> bool:
    return any(abs(t - c) <= EPS for c in cuts)


def _inside_line(t: float, lines) -> bool:
    return any(s + EPS < t < e - EPS for s, e in lines)


def _clip_text(ctx: Context, cand: Candidate) -> list[str]:
    texts = []
    for seg in cand.segments:
        ep = ctx.episodes.get(seg.episode)
        if not ep:
            texts.append("")
            continue
        texts.append(" ".join(b.text for b in ep.blocks()
                              if b.start is not None and seg.start - EPS <= b.start < seg.end - EPS))
    return texts


def validate(cand: Candidate, ctx: Context, cfg: dict) -> Validation:
    p = cfg["pipeline"]
    f: list[Check] = []
    w: list[Check] = []

    if not cand.segments:
        f.append(Check(rule="V11", detail="no segments"))
        return Validation(promo_id=cand.promo_id, passed=False, failures=f)

    for i, seg in enumerate(cand.segments, 1):
        tag = f"segment {i}"
        if seg.episode not in ctx.episodes:
            f.append(Check(rule="V1", detail=f"{tag}: episode {seg.episode} not in the input"))
            continue
        for bid in (seg.start_block, seg.end_block):
            if bid and bid not in ctx.blocks:
                f.append(Check(rule="V2", detail=f"{tag}: block {bid} does not exist"))
        if seg.end <= seg.start + EPS:
            f.append(Check(rule="V3", detail=f"{tag}: start {seg.start} is not before end {seg.end}"))
        cuts, lines = ctx.cuts[seg.episode], ctx.lines[seg.episode]
        for name, t in (("start", seg.start), ("end", seg.end)):
            if _inside_line(t, lines):
                f.append(Check(rule="V5", detail=f"{tag}: {name} {t:.2f}s cuts a spoken line"))
            elif not _on_cut(t, cuts):
                f.append(Check(rule="V4", detail=f"{tag}: {name} {t:.2f}s is not a clean cut point"))
        if seg.duration < p["min_segment"] - EPS:
            f.append(Check(rule="V6", detail=f"{tag}: {seg.duration:.2f}s is shorter than {p['min_segment']}s"))

    for bid in (cand.hook_block, cand.cliffhanger_block):
        if bid and bid not in ctx.blocks:
            f.append(Check(rule="V2", detail=f"block {bid} does not exist"))

    total = cand.total
    if not (p["min_total"] - EPS <= total <= p["max_total"] + EPS):
        f.append(Check(rule="V7", detail=f"total {total:.2f}s is outside {p['min_total']}-{p['max_total']}s"))

    by_ep: dict[int, list] = {}
    for seg in cand.segments:
        by_ep.setdefault(seg.episode, []).append(seg)
    for n, segs in by_ep.items():
        segs = sorted(segs, key=lambda s: s.start)
        for a, b in zip(segs, segs[1:]):
            if b.start < a.end - EPS:
                f.append(Check(rule="V8", detail=f"episode {n}: segments overlap at {b.start:.2f}s"))

    story = [(s.episode, s.start) for s in cand.segments]
    if story != sorted(story) and not cand.cold_open:
        f.append(Check(rule="V9", detail="segments are out of story order but not tagged as a cold open"))

    first, last = cand.segments[0], cand.segments[-1]
    hb = ctx.blocks.get(str(cand.hook_block or ""))
    if not hb:
        f.append(Check(rule="V10", detail="no valid hook block"))
    elif ctx.block_episode(hb.block_id) != first.episode or not (
            first.start - EPS <= hb.start <= first.start + p["hook_window"] + EPS):
        f.append(Check(rule="V10", detail=f"hook block {hb.block_id} does not start within "
                                          f"{p['hook_window']}s of the clip start"))
    cb = ctx.blocks.get(str(cand.cliffhanger_block or ""))
    if not cb:
        f.append(Check(rule="V11", detail="no valid cliffhanger block"))
    elif ctx.block_episode(cb.block_id) != last.episode or not (
            last.start - EPS <= cb.start and cb.end <= last.end + EPS):
        f.append(Check(rule="V11", detail=f"cliffhanger block {cb.block_id} does not play in full "
                                          f"inside the last segment"))
    if not cand.hook.strip() or not cand.cliffhanger.strip():
        f.append(Check(rule="V11", detail="hook or cliffhanger description missing"))

    # ---- warnings (do not block shipping; shown to the reviewer) ----
    texts = _clip_text(ctx, cand)
    joined = " ".join(texts).lower()
    hits = sorted({cat for cat, terms in SAFETY_TERMS.items() for t in terms if t in joined})
    if hits:
        w.append(Check(rule="W-safety", detail="brand-safety terms: " + ", ".join(hits)))
    if len(cand.episodes) > 1:
        names = [set(re.findall(r"\b[A-Z][a-z]{4,}\b", t)) for t in texts]
        clash = set()
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                for a in names[i]:
                    for b in names[j]:
                        if a != b and a[:5] == b[:5]:
                            clash.add(f"{a}/{b}")
        if clash:
            w.append(Check(rule="W-names", detail="spellings differ across episodes: " + ", ".join(sorted(clash))))
    ep0 = ctx.episodes.get(first.episode)
    if ep0 and first.start < 10 and "cliffhanger" in (ep0.continuity_notes or "").lower():
        w.append(Check(rule="W-recap", detail="opens in the first 10 s, which replays the previous episode's ending"))
    epN = ctx.episodes.get(last.episode)
    if epN and epN.duration - last.end <= 10:
        w.append(Check(rule="W-ending", detail="ends in the episode's final 10 s"))
    if cand.repairs:
        w.append(Check(rule="W-repaired", detail="; ".join(cand.repairs)))

    return Validation(promo_id=cand.promo_id, passed=not f, failures=f, warnings=w)
