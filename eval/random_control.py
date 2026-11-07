"""Chance level for the answer-key metrics: per episode, the same number of random clean-cut
20-60 s windows as a given run's gated pool, scored with the same code. Averaged over seeds."""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from promo_finder.cuts import windows  # noqa: E402
from promo_finder.evaluate import _block_times, load_ranked, reference_metrics  # noqa: E402
from promo_finder.parse import load_parsed  # noqa: E402
from promo_finder.render import clip_blocks  # noqa: E402
from promo_finder.schemas import Candidate, RankedPromo, Segment, Validation  # noqa: E402
from promo_finder.validate import Context  # noqa: E402


def main(run_dir, reference, seeds=20):
    run_dir = Path(run_dir)
    ref = json.loads(Path(reference).read_text(encoding="utf-8"))
    eps = load_parsed(run_dir / "parsed")
    ctx = Context(eps)
    blocks = _block_times(run_dir)
    rows = load_ranked(run_dir)
    per_ep = {}
    for r in rows:
        if r.status in ("shipped", "not_selected"):
            per_ep[r.candidate.segments[0].episode] = per_ep.get(r.candidate.segments[0].episode, 0) + 1
    n_ship = sum(r.status == "shipped" for r in rows)
    keys = ["hook_hit@3", "hook_hit@3_lenient", "cliffhanger_hit@3", "cliffhanger_hit@3_lenient",
            "must_promo_found", "ranked_from_strong_episodes", "slate_mean_strength"]
    tot = {k: 0.0 for k in keys}
    for seed in range(seeds):
        rnd = random.Random(seed)
        fake = []
        for n, ep in eps.items():
            wins = list(windows(ctx.cuts[n]))
            for i in range(per_ep.get(n, 0)):
                a, b = rnd.choice(wins)
                bs = [x for x in clip_blocks(ep, a, b) if x.kind != "camera"]
                if not bs:
                    continue
                c = Candidate(promo_id=f"R{n}-{i}", source="heuristic", segments=[Segment(episode=n, start=a, end=b)],
                              hook_block=bs[0].block_id, cliffhanger_block=bs[-1].block_id, hook="", cliffhanger="")
                fake.append(RankedPromo(rank=None, promo_id=c.promo_id, status="not_selected", composite=rnd.random(),
                                        candidate=c, validation=Validation(promo_id=c.promo_id, passed=True),
                                        judgement=None))
        rnd.shuffle(fake)
        for i, r in enumerate(fake[:n_ship], 1):     # a random ranked list of the same size
            r.status, r.rank, r.slate = "shipped", i, i <= 5
        m = reference_metrics(fake, ref, blocks)
        for k in keys:
            tot[k] += (m.get(k) or 0) / seeds
    out = {k: round(v, 2) for k, v in tot.items()}
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main(*sys.argv[1:3])
