"""Check one or more annotation files against the parsed episodes.
Usage: python3 eval/check_label.py eval/labels/episode-007.json [...]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from promo_finder.parse import parse_episode  # noqa: E402

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
_cache = {}


def episode(n):
    if n not in _cache:
        f = next(RAW.glob(f"episode-{n:03d}-*.txt"))
        _cache[n] = parse_episode(f).block_map()
    return _cache[n]


def check(path: Path) -> list[str]:
    probs = []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return [f"not valid JSON: {e}"]
    n = d.get("episode")
    if not isinstance(n, int):
        return ["'episode' must be an integer"]
    bm = episode(n)

    def blk(bid, what, allow_camera=False):
        b = bm.get(bid)
        if not b:
            probs.append(f"{what}: block '{bid}' does not exist in episode {n}")
        elif b.kind == "camera" and not allow_camera:
            probs.append(f"{what}: {bid} is a CAMERA block; pick dialogue, ACTION or SOUND FX")
        return b

    for k in ("best_hook", "best_cliffhanger"):
        if not isinstance(d.get(k), dict) or not d[k].get("block"):
            probs.append(f"'{k}' must be {{\"block\": ..., \"why\": ...}}")
        else:
            blk(d[k]["block"], k)
    for k in ("alt_hooks", "alt_cliffhangers"):
        v = d.get(k, [])
        if not isinstance(v, list) or len(v) > 2:
            probs.append(f"'{k}' must be a list of 0-2 block IDs")
        else:
            for x in v:
                blk(x, k)
    mp = d.get("must_promos")
    if not isinstance(mp, list) or not 1 <= len(mp) <= 2:
        probs.append("'must_promos' must hold 1 or 2 promos")
        mp = []
    for i, p in enumerate(mp, 1):
        sb = blk(p.get("start_block"), f"must_promos[{i}].start_block", allow_camera=True)
        eb = blk(p.get("end_block"), f"must_promos[{i}].end_block", allow_camera=True)
        hb = blk(p.get("hook_block"), f"must_promos[{i}].hook_block")
        cb = blk(p.get("cliffhanger_block"), f"must_promos[{i}].cliffhanger_block")
        if sb and eb:
            span = max(eb.end, sb.end) - sb.start
            if not 20 <= span <= 60:
                probs.append(f"must_promos[{i}] runs {span:.1f}s; it must be 20-60 s")
            if hb and not (sb.start - 0.05 <= hb.start <= sb.start + 1.05):
                probs.append(f"must_promos[{i}]: hook_block starts {hb.start - sb.start:.1f}s after the span start; "
                             "it must be within 1 s")
            if cb and not (sb.start <= cb.start and cb.end <= max(eb.end, sb.end) + 1.5):
                probs.append(f"must_promos[{i}]: cliffhanger_block is not at the end of the span")
    s = d.get("promo_strength")
    if not isinstance(s, int) or not 1 <= s <= 5:
        probs.append("'promo_strength' must be an integer 1-5")
    return probs


if __name__ == "__main__":
    bad = 0
    for a in sys.argv[1:]:
        p = check(Path(a))
        if p:
            bad += 1
            print(f"{a}:\n  - " + "\n  - ".join(p))
        else:
            print(f"{a}: OK")
    sys.exit(1 if bad else 0)
