"""Command line.

  python -m promo_finder run --input data/raw --out runs/llm
  python -m promo_finder run --input data/raw --out runs/baseline --mode baseline
  python -m promo_finder audit --input data/raw --out audit/
  python -m promo_finder evaluate --run runs/llm --reference reference.json --baseline runs/baseline
"""
from __future__ import annotations

import argparse
import json

from .config import load_config


def main():
    ap = argparse.ArgumentParser(prog="promo_finder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="find, judge and rank promos")
    r.add_argument("--input", required=True, help="folder or .zip of episode .txt files")
    r.add_argument("--out", required=True, help="run folder for all outputs")
    r.add_argument("--mode", choices=["llm", "baseline"], default="llm")
    r.add_argument("--config", default=None)
    r.add_argument("--provider", default=None)
    r.add_argument("--model", default=None)
    r.add_argument("--judge-model", default=None)
    r.add_argument("--reference", default=None, help="your labels, for hit@3 metrics")
    r.add_argument("--baseline", default=None, help="a baseline run folder to compare against")
    r.add_argument("--episodes", default=None, help="e.g. 1-5 or 1,10,20 to run a subset")
    r.add_argument("--title", default=None, help="series title for reports (default: input folder name)")
    pg = sub.add_parser("ping", help="check that the model and API key work (one tiny call)")
    pg.add_argument("--provider", default=None)
    pg.add_argument("--model", default=None)
    a = sub.add_parser("audit", help="structural audit of the input")
    a.add_argument("--input", required=True)
    a.add_argument("--out", default="audit")
    e = sub.add_parser("evaluate", help="recompute the evaluation of a finished run")
    e.add_argument("--run", required=True)
    e.add_argument("--reference", default=None)
    e.add_argument("--baseline", default=None)
    args = ap.parse_args()

    if args.cmd == "run":
        from .pipeline import run
        cfg = load_config(args.config, {"llm": {"provider": args.provider, "model": args.model,
                                                "judge_model": args.judge_model}})
        if cfg["llm"]["provider"] in ("gemini", "openai_compat", "anthropic") and args.mode == "llm" \
                and not cfg["llm"].get("api_key"):
            raise SystemExit("No API key found. Set GEMINI_API_KEY, ANTHROPIC_API_KEY or LLM_API_KEY, "
                             "or use --mode baseline / --provider mock.")
        if args.episodes:
            cfg["episode_filter"] = args.episodes
        if args.title:
            cfg["title"] = args.title
        m = run(args.input, args.out, cfg, mode=args.mode, reference=args.reference, baseline_dir=args.baseline)
        print(json.dumps(m["funnel"], indent=1))
    elif args.cmd == "ping":
        from .llm import LLM, LLMError
        cfg = load_config(None, {"llm": {"provider": args.provider, "model": args.model}})
        if cfg["llm"]["provider"] == "mock":
            raise SystemExit("OK: the mock provider needs no key or network.")
        cfg["llm"].update({"max_retries": 1, "timeout_s": 30})   # fail fast: this is only a check
        llm = LLM(cfg["llm"], ".cache/llm")
        try:
            text, i, o = llm._call_with_retries(
                [{"role": "user", "content": 'Reply with exactly this JSON object: {"ok": true}'}], 0.0)
            print(f"OK: {cfg['llm']['provider']} / {llm.model} answered: {text.strip()[:80]}")
        except LLMError as e:
            raise SystemExit(f"FAILED: {cfg['llm']['provider']} / {llm.model}: {e}")
    elif args.cmd == "audit":
        from .audit import main as audit_main
        audit_main(args.input, args.out)
    elif args.cmd == "evaluate":
        from .evaluate import evaluate
        ev = evaluate(args.run, reference=args.reference, reviews=f"{args.run}/reviews.json",
                      baseline_dir=args.baseline)
        print(json.dumps(ev["funnel"], indent=1))


if __name__ == "__main__":
    main()
