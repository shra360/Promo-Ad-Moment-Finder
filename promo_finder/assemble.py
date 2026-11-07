"""Stage 3: turn a proposal made of block IDs into exact, snapped segments.

The model says which blocks; this module decides the times.
- A start moves to the nearest clean cut (no one mid-line), preferring up to
  1 s earlier, then up to 1 s later, then any earlier cut; every move is logged.
- An end only ever moves LATER, to the next clean cut, so the last line or
  action always plays in full. The last segment is stretched to cover the
  whole cliffhanger block.
- A single-segment proposal over the length limit gets a jump cut that keeps
  the hook at the front and the cliffhanger at the end."""
from __future__ import annotations

from .cuts import snap
from .schemas import Candidate, Segment
from .validate import Context

EPS = 0.05


def snap_start(t: float, cuts: list[float], repairs: list[str] | None = None, tag: str = "") -> float | None:
    repairs = repairs if repairs is not None else []
    back = snap(t, cuts, "back")
    if back is not None and t - back <= 1.0:
        if t - back > EPS:
            repairs.append(f"{tag} start moved {t - back:.1f}s earlier to a clean cut")
        return back
    fwd = snap(t, cuts, "fwd")
    if fwd is not None and fwd - t <= 1.0:
        repairs.append(f"{tag} start moved {fwd - t:.1f}s later to a clean cut")
        return fwd
    if back is not None:
        repairs.append(f"{tag} start moved {t - back:.1f}s earlier: no clean cut within 1 s")
    return back


def snap_end(t: float, cuts: list[float], repairs: list[str] | None = None, tag: str = "") -> float | None:
    repairs = repairs if repairs is not None else []
    fwd = snap(t, cuts, "fwd")
    if fwd is not None and fwd - t > EPS:
        repairs.append(f"{tag} end moved {fwd - t:.1f}s later so the last line finishes")
    return fwd


def _resolve(bid, ctx: Context):
    """Accept an exact block ID, or the same ID in another case or with spaces."""
    if bid is None:
        return None
    bid = str(bid)
    if bid in ctx.blocks:
        return ctx.blocks[bid]
    norm = bid.strip().upper()
    for k in ctx.blocks:
        if k.upper() == norm:
            return ctx.blocks[k]
    return None


def build(prop: dict, promo_id: str, source: str, ctx: Context, cfg: dict) -> tuple[Candidate | None, str]:
    """Returns (candidate, "") or (None, reason). Reasons are kept as failure examples."""
    if not isinstance(prop, dict):
        return None, "proposal is not a JSON object"
    p = cfg["pipeline"]
    repairs: list[str] = []
    segs: list[Segment] = []
    raw_segs = prop.get("segments") or []
    if not isinstance(raw_segs, list) or not raw_segs:
        return None, "proposal has no segments"
    hook = _resolve(prop.get("hook_block"), ctx)
    cliff = _resolve(prop.get("cliffhanger_block"), ctx)
    for i, rs in enumerate(raw_segs, 1):
        if not isinstance(rs, dict):
            return None, f"segment {i} is not a JSON object"
        sb, eb = _resolve(rs.get("start_block"), ctx), _resolve(rs.get("end_block"), ctx)
        if not sb or not eb:
            missing = [x for x, y in ((rs.get("start_block"), sb), (rs.get("end_block"), eb)) if not y]
            return None, f"invented block ID(s): {', '.join(str(m) for m in missing)}"
        n = ctx.block_episode(sb.block_id)
        if n != ctx.block_episode(eb.block_id):
            return None, f"segment {i} starts and ends in different episodes"
        cuts = ctx.cuts[n]
        end_t = max(eb.end, sb.end)
        last = i == len(raw_segs)
        if last and cliff and ctx.block_episode(cliff.block_id) == n and cliff.start >= sb.start - EPS \
                and cliff.end > end_t + EPS:
            repairs.append(f"segment {i} extended to include the whole cliffhanger block")
            end_t = cliff.end
        s = snap_start(sb.start, cuts, repairs, f"segment {i}")
        e = snap_end(end_t, cuts, repairs, f"segment {i}")
        if s is None or e is None or e <= s:
            return None, f"segment {i} has no valid span after snapping"
        segs.append(Segment(episode=n, start=round(s, 2), end=round(e, 2),
                            start_block=sb.block_id, end_block=eb.block_id))

    cand = Candidate(
        promo_id=promo_id, source=source, title=str(prop.get("title", ""))[:120],
        beat=str(prop.get("beat", ""))[:120], segments=segs,
        hook_block=hook.block_id if hook else str(prop.get("hook_block") or ""),
        cliffhanger_block=cliff.block_id if cliff else str(prop.get("cliffhanger_block") or ""),
        hook=str(prop.get("hook", "")), cliffhanger=str(prop.get("cliffhanger", "")),
        standalone_context=str(prop.get("standalone_context", "")), why=str(prop.get("why", "")),
        cold_open=bool(prop.get("cold_open", False)), repairs=repairs,
    )

    # Too long and a single segment: keep the hook moment, jump to the run-up to the cliffhanger.
    if cand.total > p["max_total"] + EPS and len(segs) == 1 and hook and cliff:
        cand = _jump_cut(cand, hook, ctx, p) or cand
    return cand, ""


def _jump_cut(cand: Candidate, hook, ctx: Context, p: dict) -> Candidate | None:
    seg = cand.segments[0]
    cuts = ctx.cuts[seg.episode]
    head_end = snap(max(hook.end, seg.start + p["min_segment"]), cuts, "fwd")
    if head_end is None:
        return None
    room = p["max_total"] - 0.5 - (head_end - seg.start)
    tail_start = snap(seg.end - room, cuts, "fwd")
    if tail_start is None or tail_start <= head_end + p["min_segment"] or seg.end - tail_start < p["min_segment"]:
        return None
    head = Segment(episode=seg.episode, start=seg.start, end=round(head_end, 2),
                   start_block=seg.start_block, end_block=hook.block_id)
    tail = Segment(episode=seg.episode, start=round(tail_start, 2), end=seg.end,
                   start_block=None, end_block=seg.end_block)
    out = cand.model_copy(deep=True)
    out.segments = [head, tail]
    out.repairs.append(f"was {cand.total:.1f}s; jump cut after the hook to fit {p['max_total']:.0f}s "
                       f"(skips {tail_start - head_end:.1f}s)")
    return out
