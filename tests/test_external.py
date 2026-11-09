"""Provider 'external': prompts are written to files, answers are read back, the run resumes."""
import json

import pytest

from promo_finder.config import load_config
from promo_finder.pipeline import PendingStop, run

from .fixtures import write_pack


def answer_all(ex):
    for p in (ex / "pending").glob("*.json"):
        d = json.loads(p.read_text())
        a = ex / "answers" / f"{p.stem}.txt"
        a.parent.mkdir(parents=True, exist_ok=True)
        if not a.exists():
            body = {"bible": {"premise": "A test show.", "characters": [], "aliases": {}},
                    "miner": {"promos": []}, "montage": {"promos": []}}.get(d["prompt"], {})
            a.write_text(json.dumps(body))


def test_external_round_trip(tmp_path):
    cfg = load_config()
    cfg["llm"].update({"provider": "external", "model": "test-agent", "exchange_dir": str(tmp_path / "ex")})
    cfg["cache_dir"] = str(tmp_path / "cache")
    pack, out, ex = write_pack(tmp_path / "pack"), tmp_path / "out", tmp_path / "ex"
    stops = 0
    for _ in range(8):
        try:
            run(pack, out, cfg, mode="llm", progress=lambda *a, **k: None)
            break
        except PendingStop:
            stops += 1
            answer_all(ex)
    assert stops >= 2 and (out / "run_manifest.json").exists()
    prompts = {json.loads(p.read_text())["prompt"] for p in (ex / "pending").glob("*.json")}
    assert {"bible", "miner"} <= prompts
