"""Run the whole pipeline on a folder or zip of episode screenplays.

mode="llm"      : bible -> mine -> build -> validate -> montage -> judge -> rank -> evaluate
mode="baseline" : same, with the keyword baseline in place of every model call
Every stage writes its output into the run folder; model calls are cached, so a
rerun after a crash or a config change repeats only what changed."""
from __future__ import annotations

import csv
import json
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import heuristic, stages
from .audit import episode_stats
from .assemble import build
from .config import public_view
from .llm import LLM
from .parse import parse_episode
from .rank import rank
from .schemas import DIMENSIONS, Candidate, RankedPromo, fmt_ts
from .validate import Context, validate
from .evaluate import evaluate


def _usable(p: Path) -> bool:
    return p.suffix.lower() == ".txt" and not p.name.startswith(".") and "__MACOSX" not in p.parts


def load_input(path: str | Path) -> tuple[dict, str, list[str]]:
    """Returns (episodes by number, series title, parse issues). Accepts a folder
    (searched recursively) or a .zip (extracted with its folder structure)."""
    path = Path(path)
    if path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="promo_in_"))
        with zipfile.ZipFile(path) as z:
            for m in z.namelist():
                rel = Path(m)
                if rel.is_absolute() or ".." in rel.parts or not _usable(rel):
                    continue
                dest = tmp / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(m))
        root = tmp
    else:
        root = path
    files = sorted(f for f in root.rglob("*.txt") if _usable(f.relative_to(root)))
    title = files[0].parent.name if files and files[0].parent != root else path.stem
    episodes, issues = {}, []
    for f in files:
        ep = parse_episode(f)
        if ep.episode is None:
            issues.append(f"{f.name}: no episode number in the name or header; skipped")
            continue
        if ep.episode in episodes:
            issues.append(f"{f.name}: duplicate episode {ep.episode}; skipped")
            continue
        episodes[ep.episode] = ep
        issues += [f"{f.name}: {i}" for i in ep.issues]
    if not episodes:
        raise ValueError(f"No episode screenplays found in {path}")
    return episodes, title if files else path.name, issues


def run(input_path, out_dir, cfg: dict, mode: str = "llm", progress=None, reference=None,
        baseline_dir=None) -> dict:
    t0 = time.time()
    say = progress or (lambda msg, frac=None: print(msg, flush=True))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "parsed").mkdir(exist_ok=True)
    log = out / "llm_calls.jsonl"
    if log.exists():
        log.unlink()

    # 0. ingest
    episodes, title, issues = load_input(input_path)
    title = cfg.get("title") or title
    if cfg.get("episode_filter"):
        keep = parse_range(cfg["episode_filter"])
        episodes = {n: e for n, e in episodes.items() if n in keep}
        if not episodes:
            raise ValueError(f"No episodes match the filter '{cfg['episode_filter']}'")
    for n, ep in episodes.items():
        (out / "parsed" / f"episode-{n:03d}.json").write_text(json.dumps(ep.to_dict(), indent=1), encoding="utf-8")
    stats = [episode_stats(ep)[0] for ep in episodes.values()]
    with open(out / "audit.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(stats[0].keys()))
        w.writeheader()
        w.writerows(stats)
    say(f"parsed {len(episodes)} episodes of '{title}' ({len(issues)} issues)", 0.05)
    ctx = Context(episodes)

    llm = judge_llm = None
    if mode == "llm":
        cache = Path(cfg.get("cache_dir", ".cache/llm"))
        mock = stages.mock_respond if cfg["llm"]["provider"] == "mock" else None
        llm = LLM(cfg["llm"], cache, log, mock_fn=mock)
        jm = cfg["llm"].get("judge_model")
        judge_llm = LLM(cfg["llm"], cache, log, model=jm, mock_fn=mock) if jm and jm != llm.model else llm

    # 1. bible
    if llm:
        say("building the series bible", 0.08)
        bible = stages.run_bible(llm, episodes, title)
    else:
        bible = {"premise": "", "characters": [],
                 "aliases": {str(n): {b.speaker: b.speaker for b in ep.blocks() if b.kind == "dialogue"}
                             for n, ep in episodes.items()}}
    (out / "bible.json").write_text(json.dumps(bible, indent=1), encoding="utf-8")

    # 2. propose
    if llm:
        say("mining every episode", 0.12)
        mined = stages.mine_all(llm, episodes, bible, ctx, cfg,
                                progress=lambda m, f: say(m, 0.12 + 0.4 * f))
        src, prefix = "llm", "P"
    else:
        n_prop = cfg["pipeline"]["promos_per_episode"]
        mined = {n: {"episode": n, "promos": heuristic.propose(ep, n_prop, cfg["pipeline"]["min_total"],
                                                                cfg["pipeline"]["max_total"])}
                 for n, ep in episodes.items()}
        src, prefix = "heuristic", "H"
    (out / "proposals.json").write_text(json.dumps({str(k): v for k, v in sorted(mined.items())}, indent=1))
    if llm and llm.stop_reason:
        done = sum(1 for v in mined.values() if not v.get("error"))
        raise RuntimeError(f"{llm.stop_reason} ({done} of {len(mined)} episodes mined so far)")
    failed = [n for n, v in mined.items() if v.get("error")]
    if llm and len(failed) == len(mined):
        errs = "; ".join(llm.error_samples[:3]) or mined[failed[0]]["error"]
        raise RuntimeError(f"Every model call failed, so there is nothing to rank. First errors: {errs}")
    if failed:
        say(f"warning: mining failed for {len(failed)} episode(s): {sorted(failed)}", None)

    # 3. build + 4. validate
    cands: list[Candidate] = []
    failures = []
    proposed = 0
    for n in sorted(mined):
        for k, prop in enumerate(mined[n].get("promos", []), 1):
            proposed += 1
            pid = f"{prefix}{n:03d}-{k}"
            c, why = build(prop, pid, src, ctx, cfg)
            if c:
                cands.append(c)
            else:
                failures.append({"promo_id": pid, "reason": why, "proposal": prop})
    vals = {c.promo_id: validate(c, ctx, cfg) for c in cands}

    # 3b. cross-episode montage from the valid single-episode candidates
    if llm and cfg["pipeline"].get("montage_promos", 0) > 0:
        say("building cross-episode montages", 0.55)
        base = [c for c in cands if vals[c.promo_id].passed and len(c.segments) == 1]
        mont = stages.run_montage(llm, base, bible, ctx, cfg)
        (out / "montage.json").write_text(json.dumps(mont, indent=1, default=str), encoding="utf-8")
        for k, prop in enumerate(mont.get("promos", []), 1):
            proposed += 1
            pid = f"M{k:02d}"
            c, why = build(prop, pid, "montage", ctx, cfg)
            if c:
                cands.append(c)
                vals[pid] = validate(c, ctx, cfg)
            else:
                failures.append({"promo_id": pid, "reason": why, "proposal": prop})
    (out / "candidates.json").write_text(json.dumps({
        "proposed": proposed, "candidates": [c.model_dump() for c in cands], "build_failures": failures}, indent=1), encoding="utf-8")
    (out / "validation.json").write_text(json.dumps({k: v.model_dump() for k, v in vals.items()}, indent=1), encoding="utf-8")
    valid = [c for c in cands if vals[c.promo_id].passed]
    say(f"{len(cands)} candidates built, {len(valid)} valid", 0.6)

    # 5. judge
    if llm:
        say(f"judging {len(valid)} clips blind", 0.62)
        judgements = stages.judge_all(judge_llm, valid, episodes, bible, cfg,
                                      progress=lambda m, f: say(m, 0.62 + 0.3 * f))
    else:
        judgements = stages.heuristic_judge_all(valid, episodes)
    if llm and (llm.stop_reason or (judge_llm and judge_llm.stop_reason)):
        done = sum(1 for v in judgements.values() if v)
        raise RuntimeError(f"{(judge_llm or llm).stop_reason or llm.stop_reason} "
                           f"({done} of {len(judgements)} clips judged so far)")
    (out / "judgements.json").write_text(json.dumps(
        {k: (v.model_dump() if v else None) for k, v in judgements.items()}, indent=1))

    # 6. rank + exports
    rows = rank(cands, vals, judgements, episodes, cfg)
    (out / "promos_ranked.json").write_text(json.dumps([r.model_dump() for r in rows], indent=1), encoding="utf-8")
    write_csv(rows, out / "promos_ranked.csv")
    (out / "report.md").write_text(write_report(rows, title, mode, cfg, episodes, bible), encoding="utf-8")

    # 7. evaluate + manifest
    ev = evaluate(out, reference=reference, reviews=out / "reviews.json", baseline_dir=baseline_dir)
    usage = {}
    if llm:
        usage = dict(llm.usage)
        if judge_llm is not llm:
            usage = {k: usage.get(k, 0) + judge_llm.usage.get(k, 0) for k in set(usage) | set(judge_llm.usage)}
        pin, pout = cfg["llm"].get("price_in"), cfg["llm"].get("price_out")
        if pin is not None and pout is not None:
            usage["cost_usd_uncached"] = round(usage["input_tokens"] / 1e6 * pin + usage["output_tokens"] / 1e6 * pout, 4)
    manifest = {"finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "seconds": round(time.time() - t0, 1), "mode": mode, "series": title,
                "input": str(input_path), "episodes": len(episodes), "parse_issues": issues,
                "config": public_view(cfg), "usage": usage, "funnel": ev["funnel"],
                "failures": {"bible": bible.get("_error"),
                             "mining_failed_episodes": sorted(n for n, v in mined.items() if v.get("error")),
                             "montage": (json.loads((out / "montage.json").read_text(encoding="utf-8")).get("error")
                                         if (out / "montage.json").exists() else None),
                             "judge_failed": sorted(k for k, v in judgements.items() if v is None),
                             "model_errors": (llm.error_samples if llm else [])}}
    (out / "run_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    say(f"done: {ev['funnel']['shipped']} promos shipped in {manifest['seconds']}s", 1.0)
    return manifest


def parse_range(spec: str) -> set[int]:
    out = set()
    for part in str(spec).split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return out


def seg_text(c: Candidate) -> str:
    return " | ".join(f"Ep {s.episode} {fmt_ts(s.start)}-{fmt_ts(s.end)}" for s in c.segments)


def write_csv(rows: list[RankedPromo], path: Path):
    cols = ["rank", "status", "promo_id", "source", "title", "segments", "total_s", "hook", "cliffhanger",
            "why", "composite"] + [f"score_{k}" for k in DIMENSIONS] + ["review_flags", "duplicate_of", "failures"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            c, j = r.candidate, r.judgement
            w.writerow([r.rank or "", r.status, r.promo_id, c.source, c.title, seg_text(c), c.total, c.hook,
                        c.cliffhanger, c.why, r.composite if r.composite is not None else ""] +
                       [j.scores.get(k, "") if j else "" for k in DIMENSIONS] +
                       ["; ".join(r.review_flags), r.duplicate_of or "",
                        "; ".join(f.detail for f in r.validation.failures)])


def write_report(rows: list[RankedPromo], title: str, mode: str, cfg: dict, episodes, bible) -> str:
    shipped = [r for r in rows if r.status == "shipped"]
    who = cfg["llm"]["model"] if mode == "llm" else "no-LLM baseline"
    L = [f"# Promos for {title}", "", f"Mode: {mode} ({who}). {len(shipped)} promos ranked from "
         f"{len(rows)} candidates. Times are MM:SS.ff from the start of each episode.", ""]
    L += ["## Post these first", ""]
    for r in shipped:
        if not r.slate:
            continue
        c, j = r.candidate, r.judgement
        L += [f"### {r.rank}. {c.title}  (score {r.composite:.2f})", "",
              f"- **Cut:** {seg_text(c)} — {c.total:.1f} s",
              f"- **Hook (first 3 s):** {c.hook}",
              f"- **Cliffhanger:** {c.cliffhanger}",
              f"- **Why it works:** {c.why}"]
        if j and j.samples and j.samples[0].pitch and j.judge != "heuristic":
            L.append(f"- **Judge's pitch:** {j.samples[0].pitch}")
        if j:
            L.append("- **Scores:** " + ", ".join(f"{k} {j.scores[k]:.1f}" for k in DIMENSIONS))
        if r.review_flags:
            L.append("- **Check before posting:** " + "; ".join(r.review_flags))
        L.append("")
    L += ["## Full ranked list", "", "| # | Promo | Cut | Length | Score | Flags |", "| --- | --- | --- | --- | --- | --- |"]
    for r in shipped:
        L.append(f"| {r.rank} | {r.candidate.title} | {seg_text(r.candidate)} | {r.candidate.total:.1f}s | "
                 f"{r.composite:.2f} | {len(r.review_flags)} |")
    L += ["", "Rejected, below-gate and duplicate candidates are in promos_ranked.csv with reasons; "
              "eval_report.md lists where the tool picks badly.", ""]
    return "\n".join(L)
