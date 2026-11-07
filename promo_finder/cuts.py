"""Clean cut points (deterministic). A cut is clean when it sits on a block
boundary and no spoken line is mid-delivery at that instant. Promo segments may
only start and end on clean cuts, so code - not the model - decides exact times."""
from __future__ import annotations

from .parse import Episode

EPS = 0.05


def clean_cut_points(ep: Episode) -> list[float]:
    blocks = [b for b in ep.blocks() if b.start is not None and b.end is not None]
    lines = [(b.start, b.end) for b in blocks if b.kind == "dialogue"]
    bounds = sorted({round(t, 2) for b in blocks for t in (b.start, b.end)})
    if ep.scenes and ep.scenes[-1].end is not None:
        bounds.append(round(ep.scenes[-1].end, 2))
    return sorted({t for t in bounds if not any(s + EPS < t < e - EPS for s, e in lines)})


def snap(t: float, cuts: list[float], direction: str) -> float | None:
    """Nearest clean cut at or before t ('back') or at or after t ('fwd')."""
    if direction == "back":
        c = [x for x in cuts if x <= t + EPS]
        return c[-1] if c else None
    c = [x for x in cuts if x >= t - EPS]
    return c[0] if c else None


def windows(cuts: list[float], lo: float = 20.0, hi: float = 60.0):
    """All single-segment (start, end) pairs of clean cuts lasting lo..hi seconds."""
    for i, a in enumerate(cuts):
        for b in cuts[i + 1:]:
            d = b - a
            if d > hi + EPS:
                break
            if d >= lo - EPS:
                yield a, b
