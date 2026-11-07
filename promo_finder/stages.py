"""LLM stages: series bible, per-episode mining, cross-episode montage, blind judge.
Each stage builds its prompt variables, gives the model one chance to fix any
problem code can detect (unknown block IDs, wrong lengths, missing scores), and
returns plain dicts. Hidden variables (keys starting with "_") feed the mock."""
from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import heuristic
from .llm import LLM, LLMError
from .parse import Episode
from .render import clip_blocks, clip_text, episode_text, sorted_blocks
from .schemas import DIMENSIONS, Candidate, Judgement, JudgeSample
from .validate import Context

EPS = 0.05


def _short(t: str, n: int) -> str:
    w = " ".join((t or "").split()).split()
    return " ".join(w[:n]) + ("..." if len(w) > n else "")


# ---------------------------------------------------------------- bible
def bible_input(episodes: dict[int, Episode]) -> str:
    out = []
    for n in sorted(episodes):
        ep = episodes[n]
        out.append(f"EP {n} | logline: {ep.logline or '-'}")
        out.append(f"  continuity: {ep.continuity_notes or '-'}")
        bs = sorted_blocks(ep)
        counts = Counter(b.speaker for b in bs if b.kind == "dialogue")
        for label, c in counts.most_common():
            idx = next(i for i, b in enumerate(bs) if b.kind == "dialogue" and b.speaker == label)
            cam = next((b.text for b in reversed(bs[:idx]) if b.kind == "camera"), "")
            line = bs[idx].text
            out.append(f"  - {label} ({c} lines). Camera before first line: \"{_short(cam, 20)}\" "
                       f"First line: {_short(line, 14)}")
    return "\n".join(out)


def run_bible(llm: LLM, episodes: dict[int, Episode], title: str) -> dict:
    labels = {str(n): {b.speaker for b in ep.blocks() if b.kind == "dialogue"} for n, ep in episodes.items()}

    def check(d):
        probs = []
        al = d.get("aliases") if isinstance(d.get("aliases"), dict) else {}
        if not al:
            probs.append("'aliases' must map each episode number to {label: name}")
        for n, labs in labels.items():
            got_map = al.get(n) or al.get(int(n)) or {}
            got = {str(k).upper() for k in got_map} if isinstance(got_map, dict) else set()
            miss = sorted(l for l in labs if l.upper() not in got)
            if miss:
                probs.append(f"episode {n}: no mapping for {', '.join(miss)}")
        if not d.get("premise"):
            probs.append("premise is missing")
        return probs

    try:
        r = llm.complete_json("bible", {"title": title, "episodes": bible_input(episodes),
                                        "_episodes": episodes}, check=check, tag="series")
        bible = r["data"]
        bible["_unfixed_problems"] = r["problems"]
    except Exception as e:  # a failed bible degrades to raw labels; it must not stop the run
        bible = {"premise": "", "characters": [], "aliases": {}, "_error": f"{type(e).__name__}: {e}"}
    if not isinstance(bible.get("characters"), list):
        bible["characters"] = []
    bible["premise"] = str(bible.get("premise") or "")
    # normalise: string episode keys, exact label case from the script, identity fallback
    raw = bible.get("aliases") if isinstance(bible.get("aliases"), dict) else {}
    norm = {}
    for n, labs in labels.items():
        given = raw.get(n) or raw.get(int(n)) or {}
        up = {str(k).upper(): str(v) for k, v in given.items()} if isinstance(given, dict) else {}
        norm[n] = {l: up.get(l.upper(), l.title()) for l in labs}
    bible["aliases"] = norm
    return bible


def characters_line(bible: dict, limit: int = 25) -> str:
    chars = bible.get("characters") or []
    return "; ".join(f"{c.get('name')}: {c.get('who', '')}" for c in chars[:limit] if isinstance(c, dict)) or "-"


# ---------------------------------------------------------------- mining
def _span_secs(ctx: Context, sb, eb) -> float | None:
    a, b = ctx.blocks.get(str(sb)), ctx.blocks.get(str(eb))
    return None if not a or not b else max(b.end, a.end) - a.start


def check_proposals(d: dict, ctx: Context, cfg: dict, episodes_allowed: set[int] | None,
                    want: int, min_eps: int = 1) -> list[str]:
    p, probs = cfg["pipeline"], []
    promos = d.get("promos")
    if not isinstance(promos, list) or not promos:
        return ["'promos' must be a non-empty list"]
    if len(promos) < want - 1:
        probs.append(f"you returned {len(promos)} promos; {want} were asked for")
    for i, pr in enumerate(promos, 1):
        if not isinstance(pr, dict):
            probs.append(f"promo {i}: must be a JSON object")
            continue
        segs = pr.get("segments") or []
        if not isinstance(segs, list) or not segs or not all(isinstance(x, dict) for x in segs):
            probs.append(f"promo {i}: 'segments' must be a list of {{start_block, end_block}} objects")
            continue
        total, eps_used = 0.0, set()
        for j, s in enumerate(segs, 1):
            sb, eb = str(s.get("start_block", "")), str(s.get("end_block", ""))
            for bid in (sb, eb):
                if bid not in ctx.blocks:
                    probs.append(f"promo {i} segment {j}: block ID '{bid}' does not exist")
                elif episodes_allowed and ctx.block_episode(bid) not in episodes_allowed:
                    probs.append(f"promo {i} segment {j}: block '{bid}' is from another episode")
            secs = _span_secs(ctx, sb, eb)
            if secs is not None:
                if secs <= 0:
                    probs.append(f"promo {i} segment {j}: end_block comes before start_block")
                total += max(secs, 0)
                eps_used.add(ctx.block_episode(sb))
        if total and not (p["min_total"] - 1 <= total <= p["max_total"] + 1):
            probs.append(f"promo {i}: spans add up to {total:.1f}s; it must be {p['min_total']:.0f}-{p['max_total']:.0f}s")
        if len(eps_used) < min_eps:
            probs.append(f"promo {i}: must use at least {min_eps} different episodes")
        hb, first = ctx.blocks.get(str(pr.get("hook_block", ""))), ctx.blocks.get(str(segs[0].get("start_block", "")))
        if not hb:
            probs.append(f"promo {i}: hook_block '{pr.get('hook_block')}' does not exist")
        elif first:
            # measure against the start code will actually use (snapped to a clean cut)
            from .assemble import snap_start
            n0 = ctx.block_episode(first.block_id)
            s0 = snap_start(first.start, ctx.cuts.get(n0, []), []) if n0 in ctx.cuts else first.start
            s0 = first.start if s0 is None else s0
            if not (-EPS <= hb.start - s0 <= p["hook_window"] + EPS):
                probs.append(f"promo {i}: hook_block {hb.block_id} starts {hb.start - s0:.1f}s after the clip "
                             f"would start (the nearest clean cut is {s0:.2f}s); it must be within "
                             f"{p['hook_window']:.0f}s, so start the span at or just before the hook")
        cb = ctx.blocks.get(str(pr.get("cliffhanger_block", "")))
        lsb, leb = ctx.blocks.get(str(segs[-1].get("start_block", ""))), ctx.blocks.get(str(segs[-1].get("end_block", "")))
        if not cb:
            probs.append(f"promo {i}: cliffhanger_block '{pr.get('cliffhanger_block')}' does not exist")
        elif lsb and leb and not (lsb.start - EPS <= cb.start <= leb.end + EPS):
            probs.append(f"promo {i}: cliffhanger_block {cb.block_id} is not inside the last span")
        for k in ("hook", "cliffhanger", "why"):
            if not str(pr.get(k, "")).strip():
                probs.append(f"promo {i}: '{k}' is empty")
    return probs


def mine_episode(llm: LLM, n: int, episodes: dict[int, Episode], bible: dict, ctx: Context, cfg: dict) -> dict:
    ep = episodes[n]
    al = bible["aliases"].get(str(n), {})
    prev_ep, next_ep = episodes.get(n - 1), episodes.get(n + 1)
    want = cfg["pipeline"]["promos_per_episode"]
    variables = {
        "premise": bible.get("premise") or "-",
        "characters": characters_line(bible),
        "prev": f"Ep {n - 1}: {prev_ep.logline}" if prev_ep else "none (this is the first episode in the input)",
        "next": f"Ep {n + 1}: {next_ep.logline}" if next_ep else "none in the input",
        "episode": episode_text(ep, al), "n": want,
        "min_total": int(cfg["pipeline"]["min_total"]), "max_total": int(cfg["pipeline"]["max_total"]),
        "_episode": ep,
    }
    r = llm.complete_json("miner", variables, tag=f"ep{n}",
                          check=lambda d: check_proposals(d, ctx, cfg, {n}, want))
    promos = r["data"].get("promos")
    promos = [x for x in promos if isinstance(x, dict)] if isinstance(promos, list) else []
    return {"episode": n, "promos": promos, "unfixed_problems": r["problems"],
            "rounds": len(r["attempts"]), "raw_first_answer": r["attempts"][0]}


def mine_all(llm: LLM, episodes, bible, ctx, cfg, progress=None) -> dict[int, dict]:
    out = {}
    with ThreadPoolExecutor(max_workers=int(cfg["llm"].get("max_workers", 4))) as ex:
        futs = {ex.submit(mine_episode, llm, n, episodes, bible, ctx, cfg): n for n in sorted(episodes)}
        for k, f in enumerate(as_completed(futs), 1):
            n = futs[f]
            try:
                out[n] = f.result()
            except Exception as e:  # one bad episode must not stop the other 49
                out[n] = {"episode": n, "promos": [], "error": f"{type(e).__name__}: {e}"}
            if progress:
                progress(f"mined episode {n}", k / len(futs))
    return out


# ---------------------------------------------------------------- montage
def montage_index(cands: list[Candidate], ctx: Context, limit: int = 150) -> tuple[str, list[dict]]:
    """Round-robin over episodes so late episodes are represented, not just the first 30."""
    by_ep: dict[int, list[Candidate]] = {}
    for c in cands:
        by_ep.setdefault(c.segments[0].episode, []).append(c)
    ordered, depth = [], 0
    while len(ordered) < limit and any(depth < len(v) for v in by_ep.values()):
        ordered += [v[depth] for _, v in sorted(by_ep.items()) if depth < len(v)]
        depth += 1
    lines, items = [], []
    for c in ordered[:limit]:
        seg = c.segments[0] if len(c.segments) == 1 else None
        hb, cb = ctx.blocks.get(c.hook_block or ""), ctx.blocks.get(c.cliffhanger_block or "")
        if not seg or not hb or not cb:
            continue
        ep = ctx.episodes[seg.episode]
        bs = clip_blocks(ep, seg.start, seg.end)
        hook_end = next((b for b in bs if b.end >= hb.start + 6), bs[-1])
        tail_start = next((b for b in bs if b.start >= cb.end - 18), bs[0])
        spans = [("full", bs[0], bs[-1]), ("hook", hb, hook_end), ("tail", tail_start, cb)]
        spans_txt = "; ".join(f"{k} {a.block_id}..{b.block_id} ({max(b.end, a.end) - a.start:.1f}s)"
                              for k, a, b in spans if b.end > a.start)
        lines.append(f"{c.promo_id} | ep {seg.episode} | {c.beat or c.title} | hook: {_short(c.hook, 16)} | "
                     f"cliffhanger: {_short(c.cliffhanger, 16)} | spans: {spans_txt}")
        items.append({"cand": c, "spans": spans})
    return "\n".join(lines), items


def run_montage(llm: LLM, cands: list[Candidate], bible: dict, ctx: Context, cfg: dict) -> dict:
    n = int(cfg["pipeline"].get("montage_promos", 0))
    if n <= 0 or len({c.segments[0].episode for c in cands}) < 2:
        return {"promos": [], "skipped": True}
    index, items = montage_index(cands, ctx)
    try:
        r = llm.complete_json("montage", {"n": n, "premise": bible.get("premise") or "-", "index": index,
                                          "_items": items}, tag="montage",
                              check=lambda d: check_proposals(d, ctx, cfg, None, n, min_eps=2))
        promos = r["data"].get("promos")
        promos = [x for x in promos if isinstance(x, dict)] if isinstance(promos, list) else []
        return {"promos": promos, "unfixed_problems": r["problems"], "raw_first_answer": r["attempts"][0]}
    except Exception as e:
        return {"promos": [], "error": f"{type(e).__name__}: {e}"}


# ---------------------------------------------------------------- judge
def _clip_ids(cand: Candidate, episodes) -> set[str]:
    return {b.block_id for s in cand.segments for b in clip_blocks(episodes[s.episode], s.start, s.end)}


def _score(v) -> int | None:
    """4, 4.0, "4", "4/5" -> 4; anything else -> None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = v
    else:
        m = re.match(r"\s*(\d+(?:\.\d+)?)", str(v))
        if not m:
            return None
        x = float(m.group(1))
    x = int(round(x))
    return x if 1 <= x <= 5 else None


def _evidence(d: dict) -> dict[str, list[str]]:
    ev = d.get("evidence") if isinstance(d.get("evidence"), dict) else {}
    out = {}
    for k, v in ev.items():
        items = v if isinstance(v, list) else [v]
        out[str(k)] = [str(x) for x in items if isinstance(x, (str, int))]
    return out


def check_judge(d: dict, ids: set[str]) -> list[str]:
    probs = []
    sc = d.get("scores") if isinstance(d.get("scores"), dict) else {}
    for k in DIMENSIONS:
        if _score(sc.get(k)) is None:
            probs.append(f"scores.{k} must be a whole number from 1 to 5")
    bad = sorted({b for v in _evidence(d).values() for b in v if b not in ids})
    if bad:
        probs.append(f"evidence cites blocks that are not in the clip: {', '.join(bad[:10])}")
    return probs


def aggregate(promo_id: str, judge_name: str, samples: list[JudgeSample], ids: set[str]) -> Judgement:
    scores = {k: round(sum(s.scores.get(k, 0) for s in samples) / len(samples), 2) for k in DIMENSIONS}
    spread = {k: max(s.scores.get(k, 0) for s in samples) - min(s.scores.get(k, 0) for s in samples)
              for k in DIMENSIONS}
    cited = {b for s in samples for v in s.evidence.values() for b in v}
    notes = []
    if sum(s.resolves_tension for s in samples) * 2 > len(samples):
        notes.append("judge says the clip resolves its own tension")
    if sum(s.needs_prior_context for s in samples) * 2 > len(samples):
        notes.append("judge says it needs earlier episodes")
    return Judgement(promo_id=promo_id, judge=judge_name, samples=samples, scores=scores, spread=spread,
                     evidence_ok=cited <= ids if judge_name != "heuristic" else True, notes=notes)


def judge_one(llm: LLM, cand: Candidate, episodes, bible, cfg) -> Judgement:
    ids = _clip_ids(cand, episodes)
    clip = clip_text(cand, episodes, bible.get("aliases"), blind=True)
    n = int(cfg["pipeline"].get("judge_samples", 1))
    samples = []
    for s in range(n):
        temp = cfg["llm"].get("temperature", 0.2) if s == 0 else 0.7
        r = llm.complete_json("judge", {"clip": clip, "_candidate": cand, "_episodes": episodes},
                              temperature=temp, sample=s, tag=cand.promo_id,
                              check=lambda d: check_judge(d, ids))
        d = r["data"]
        raw = d.get("scores") if isinstance(d.get("scores"), dict) else {}
        sc = {k: _score(raw.get(k)) for k in DIMENSIONS}
        if any(v is None for v in sc.values()):
            continue          # an unusable sample is dropped, not guessed
        reasons = d.get("reasons") if isinstance(d.get("reasons"), dict) else {}
        flags = d.get("safety_flags") if isinstance(d.get("safety_flags"), list) else []
        samples.append(JudgeSample(
            scores=sc,
            reasons={str(k): str(v) for k, v in reasons.items()},
            evidence=_evidence(d),
            resolves_tension=bool(d.get("resolves_tension")),
            needs_prior_context=bool(d.get("needs_prior_context")),
            safety_flags=[str(x) for x in flags],
            pitch=str(d.get("pitch", ""))))
    if not samples:
        raise LLMError(f"no usable judge sample for {cand.promo_id}")
    return aggregate(cand.promo_id, llm.model, samples, ids)


def judge_all(llm: LLM, cands: list[Candidate], episodes, bible, cfg, progress=None) -> dict[str, Judgement]:
    out = {}
    with ThreadPoolExecutor(max_workers=int(cfg["llm"].get("max_workers", 4))) as ex:
        futs = {ex.submit(judge_one, llm, c, episodes, bible, cfg): c.promo_id for c in cands}
        for k, f in enumerate(as_completed(futs), 1):
            pid = futs[f]
            try:
                out[pid] = f.result()
            except Exception as e:
                out[pid] = None
                if progress:
                    progress(f"judge failed for {pid}: {type(e).__name__}: {e}", k / len(futs))
            if progress:
                progress(f"judged {pid}", k / len(futs))
    return out


def heuristic_judge_all(cands: list[Candidate], episodes) -> dict[str, Judgement]:
    return {c.promo_id: aggregate(c.promo_id, "heuristic", [heuristic.judge(c, episodes)], set())
            for c in cands}


# ---------------------------------------------------------------- mock model (tests, offline demo)
def mock_respond(prompt_name: str, v: dict, sample: int, round_: int) -> dict:
    if prompt_name == "bible":
        eps = v["_episodes"]
        return {"premise": eps[min(eps)].logline or "",
                "characters": [],
                "aliases": {str(n): {b.speaker: (b.speaker or "").title() for b in ep.blocks() if b.kind == "dialogue"}
                            for n, ep in eps.items()}}
    if prompt_name == "miner":
        return {"promos": heuristic.propose(v["_episode"], int(v["n"]))}
    if prompt_name == "montage":
        items = v["_items"]
        out = []
        for a in items:
            for b in items:
                ca, cb = a["cand"], b["cand"]
                if ca.segments[0].episode < cb.segments[0].episode:
                    h, t = a["spans"][1], b["spans"][2]
                    out.append({"title": f"{ca.title} / {cb.title}", "beat": f"{ca.beat} then {cb.beat}",
                                "segments": [{"start_block": h[1].block_id, "end_block": h[2].block_id},
                                             {"start_block": t[1].block_id, "end_block": t[2].block_id}],
                                "hook_block": ca.hook_block, "cliffhanger_block": cb.cliffhanger_block,
                                "hook": ca.hook, "cliffhanger": cb.cliffhanger,
                                "standalone_context": "mock", "why": "mock montage"})
                    break
            if len(out) >= int(v["n"]):
                break
        return {"promos": out}
    if prompt_name == "judge":
        return heuristic.judge(v["_candidate"], v["_episodes"]).model_dump()
    raise ValueError(prompt_name)
