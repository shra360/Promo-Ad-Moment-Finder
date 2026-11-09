"""Helpers for provider 'external' (answers written as files instead of API calls).

  python eval/agent_exchange.py batches [--exchange .exchange] [--miner 4] [--judge 18] [--other 3]
      -> writes .exchange/batches/<prompt>-NN.txt, each listing pending prompt files still unanswered
  python eval/agent_exchange.py check <answer.txt> [...]
      -> each answer must be exactly one JSON object
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def batches(ex: Path, sizes: dict):
    pend = sorted((ex / "pending").glob("*.json"))
    todo = [p for p in pend if not (ex / "answers" / f"{p.stem}.txt").exists()]
    groups = defaultdict(list)
    for p in todo:
        d = json.loads(p.read_text(encoding="utf-8"))
        kind = d.get("prompt") or "other"
        groups[(kind, d.get("round", 1) > 1)].append(p)
    out = ex / "batches"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.txt"):
        old.unlink()
    made = []
    for (kind, corr), files in sorted(groups.items()):
        n = sizes.get(kind, sizes["other"]) if not corr else sizes["other"]
        for i in range(0, len(files), n):
            name = f"{kind}{'-fix' if corr else ''}-{i // n + 1:02d}.txt"
            (out / name).write_text("\n".join(str(f.resolve()) for f in files[i:i + n]) + "\n", encoding="utf-8")
            made.append((name, len(files[i:i + n])))
    (ex / "answers").mkdir(parents=True, exist_ok=True)
    print(f"{len(todo)} unanswered of {len(pend)} pending; {len(made)} batches")
    for n, k in made:
        print(f"  {n}: {k}")


def check(paths):
    bad = 0
    for a in paths:
        t = Path(a).read_text(encoding="utf-8").strip()
        try:
            ok = isinstance(json.loads(t), dict)
        except Exception as e:
            ok, err = False, str(e)
        else:
            err = "" if ok else "not a JSON object"
        print(f"{a}: {'OK' if ok else 'BAD - ' + err}")
        bad += not ok
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "check":
        check(a[1:])
    else:
        ex = Path(a[a.index("--exchange") + 1]) if "--exchange" in a else Path(".exchange")
        sz = {"miner": 4, "judge": 18, "bible": 1, "montage": 1, "other": 3}
        for k in ("miner", "judge", "other"):
            if f"--{k}" in a:
                sz[k] = int(a[a.index(f"--{k}") + 1])
        batches(ex, sz)
