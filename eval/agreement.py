"""How far do two independent annotators agree? This is the ceiling for any
tool scored against one of them. Usage: python eval/agreement.py eval/labels eval/labels_b"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from promo_finder.evaluate import spearman  # noqa: E402
from promo_finder.parse import parse_episode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TOL = 3.0


def main(a_dir, b_dir, raw=ROOT / "data" / "raw"):
    eps = {}
    for f in sorted(Path(raw).rglob("*.txt")):
        e = parse_episode(f)
        if e.episode is not None:
            eps[e.episode] = e.block_map()
    rows = []
    for fb in sorted(Path(b_dir).glob("episode-*.json")):
        fa = Path(a_dir) / fb.name
        if not fa.exists():
            continue
        A, B = (json.loads(x.read_text(encoding="utf-8")) for x in (fa, fb))
        bm = eps[A["episode"]]

        def t(bid, field):
            return getattr(bm[bid], field)

        def near(x, ys, field):
            return any(abs(t(x, field) - t(y, field)) <= TOL for y in ys)

        r = {"episode": A["episode"]}
        for kind, field in (("hook", "start"), ("cliffhanger", "end")):
            a_best, b_best = A[f"best_{kind}"]["block"], B[f"best_{kind}"]["block"]
            a_all = [a_best] + A.get(f"alt_{kind}s", [])
            b_all = [b_best] + B.get(f"alt_{kind}s", [])
            r[f"{kind}_best_agree"] = near(a_best, [b_best], field)
            r[f"{kind}_any_agree"] = any(near(x, b_all, field) for x in a_all)

        def spans(d):
            return [(t(p["start_block"], "start"), max(t(p["end_block"], "end"), t(p["start_block"], "end")))
                    for p in d["must_promos"]]
        sa, sb = spans(A), spans(B)
        r["must_overlap"] = any(min(a1, b1) - max(a0, b0) >= 0.5 * min(a1 - a0, b1 - b0)
                                for a0, a1 in sa for b0, b1 in sb)
        r["strength_a"], r["strength_b"] = A["promo_strength"], B["promo_strength"]
        rows.append(r)
    n = len(rows)
    share = lambda k: round(sum(r[k] for r in rows) / n, 2) if n else None
    out = {
        "episodes_compared": n, "tolerance_s": TOL,
        "hook_best_agree": share("hook_best_agree"), "hook_any_agree": share("hook_any_agree"),
        "cliffhanger_best_agree": share("cliffhanger_best_agree"), "cliffhanger_any_agree": share("cliffhanger_any_agree"),
        "must_promo_overlap": share("must_overlap"),
        "strength_exact": round(sum(r["strength_a"] == r["strength_b"] for r in rows) / n, 2) if n else None,
        "strength_within_1": round(sum(abs(r["strength_a"] - r["strength_b"]) <= 1 for r in rows) / n, 2) if n else None,
        "strength_spearman": spearman([float(r["strength_a"]) for r in rows], [float(r["strength_b"]) for r in rows]),
        "per_episode": rows,
    }
    print(json.dumps({k: v for k, v in out.items() if k != "per_episode"}, indent=1))
    (ROOT / "eval" / "agreement.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    main(*sys.argv[1:3])
