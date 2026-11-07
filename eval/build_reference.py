"""Merge eval/labels/*.json (one answer-key file per episode) into eval/reference.json,
the format `python -m promo_finder run --reference` and `evaluate` read.
Usage: python eval/build_reference.py [labels_dir] [raw_dir] [out_file]"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from promo_finder.parse import parse_episode  # noqa: E402


def main(labels=ROOT / "eval" / "labels", raw=ROOT / "data" / "raw", out=ROOT / "eval" / "reference.json"):
    labels, raw, out = Path(labels), Path(raw), Path(out)
    eps = {}
    for f in sorted(raw.rglob("*.txt")):
        e = parse_episode(f)
        if e.episode is not None:
            eps[e.episode] = e.block_map()
    ref = {"source": f"answer key from {labels}", "episodes": {}}
    for f in sorted(labels.glob("episode-*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        bm = eps[d["episode"]]
        spans = []
        for p in d.get("must_promos", []):
            a, b = bm[p["start_block"]], bm[p["end_block"]]
            spans.append([round(a.start, 2), round(max(a.end, b.end), 2)])
        ref["episodes"][str(d["episode"])] = {
            "hook": d["best_hook"]["block"], "alt_hooks": d.get("alt_hooks", []),
            "cliffhanger": d["best_cliffhanger"]["block"], "alt_cliffhangers": d.get("alt_cliffhangers", []),
            "must_promo": spans, "promo_strength": d.get("promo_strength")}
    out.write_text(json.dumps(ref, indent=1), encoding="utf-8")
    print(f"{len(ref['episodes'])} episodes -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:])
