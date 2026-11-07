"""Regression tests for the independent code review's findings (numbers match the review)."""
import json
import zipfile

import pytest

from promo_finder import stages
from promo_finder.assemble import build
from promo_finder.config import load_config
from promo_finder.evaluate import reference_metrics
from promo_finder.llm import LLM, LLMError, _retryable
from promo_finder.parse import parse_episode
from promo_finder.pipeline import load_input, run
from promo_finder.schemas import Candidate, RankedPromo, Segment, Validation, Judgement
from promo_finder.validate import Context, validate

from .fixtures import episode_text, write_pack


@pytest.fixture()
def cfg(tmp_path):
    c = load_config()
    c["llm"]["provider"] = "mock"
    c["cache_dir"] = str(tmp_path / "cache")
    return c


@pytest.fixture()
def ctx(tmp_path):
    return Context(load_input(write_pack(tmp_path / "pack"))[0])


# 1. the cliffhanger always plays in full
def test_cliffhanger_block_plays_in_full(ctx, cfg):
    bm = ctx.episodes[1].block_map()
    lines = [b for b in bm.values() if b.kind == "dialogue"]
    first, cliff = lines[1], lines[6]
    # end_block is a camera shot that ends before the cliffhanger line does
    before = max((b for b in bm.values() if b.kind == "camera" and b.end < cliff.end), key=lambda b: b.end)
    prop = {"segments": [{"start_block": first.block_id, "end_block": before.block_id}],
            "hook_block": first.block_id, "cliffhanger_block": cliff.block_id, "hook": "h", "cliffhanger": "c"}
    c, _ = build(prop, "R1", "llm", ctx, cfg)
    assert c.segments[-1].end >= cliff.end
    assert any("whole cliffhanger" in r for r in c.repairs)
    # and the validator rejects a clip that cuts the cliffhanger short
    c.segments[-1].end = cliff.start + 0.5
    assert "V11" in {f.rule for f in validate(c, ctx, cfg).failures} or not validate(c, ctx, cfg).passed


def test_end_never_moves_earlier(ctx):
    from promo_finder.assemble import snap_end
    cuts = ctx.cuts[1]
    for t in [x + 0.3 for x in cuts[:-1]]:
        e = snap_end(t, cuts)
        assert e is None or e >= t


# 2. malformed answers never crash a run
def odd_mock(name, v, sample, round_):
    if name == "bible":
        return {"premise": None, "characters": "nobody", "aliases": None}
    if name == "miner":
        return {"promos": [["not", "a", "dict"], {"segments": "nope", "hook_block": 12345}]}
    if name == "montage":
        return {"promos": "none"}
    if name == "judge":
        return {"scores": {k: "4/5" for k in ("hook", "cliffhanger", "standalone", "coherence", "intensity",
                                              "curiosity")},
                "evidence": {"hook": [{"id": "x"}], "cliffhanger": "E001-S01-B001"}, "reasons": "n/a"}
    raise ValueError(name)


def test_odd_answers_do_not_crash(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(stages, "mock_respond", odd_mock)
    pack = write_pack(tmp_path / "pack")
    run(pack, tmp_path / "out", cfg, mode="llm", progress=lambda *a, **k: None)
    m = json.loads((tmp_path / "out" / "run_manifest.json").read_text())
    assert m["funnel"]["shipped"] == 0 and m["funnel"]["build_failures"] >= 1


def test_string_scores_are_parsed():
    assert stages._score("4/5") == 4 and stages._score(3.6) == 4 and stages._score("x") is None
    assert stages._score(True) is None and stages._score(9) is None


# 3. retries follow the HTTP status
class FakeErr(Exception):
    def __init__(self, msg, status=None):
        super().__init__(msg)
        self.status_code = status


class APITimeoutError(Exception):
    pass


def test_retry_decisions():
    assert not _retryable(FakeErr("models/x is not found for API version v1beta, or not supported for "
                                  "generateContent", 404))
    assert _retryable(FakeErr("quota", 429)) and _retryable(FakeErr("busy", 503))
    assert not _retryable(FakeErr("bad request", 400))
    assert _retryable(APITimeoutError("Request timed out."))


# 4. a run where every model call fails says so
def test_total_failure_raises(tmp_path, cfg, monkeypatch):
    def broken(*a, **k):
        raise LLMError("AuthenticationError: 401 invalid key")
    monkeypatch.setattr(LLM, "_complete", broken)
    with pytest.raises(RuntimeError, match="Every model call failed"):
        run(write_pack(tmp_path / "pack"), tmp_path / "out", cfg, mode="llm", progress=lambda *a, **k: None)


# 6. the montage index reaches late episodes
def test_montage_index_round_robin(tmp_path):
    pack = write_pack(tmp_path / "pack", n=6)
    ctx = Context(load_input(pack)[0])
    cands = []
    for n in range(1, 7):
        bm = [b for b in ctx.episodes[n].block_map().values() if b.kind == "dialogue"]
        for k in range(5):
            s = Segment(episode=n, start=bm[0].start, end=bm[5].end)
            cands.append(Candidate(promo_id=f"P{n}-{k}", source="llm", segments=[s], hook_block=bm[0].block_id,
                                   cliffhanger_block=bm[5].block_id, hook="h", cliffhanger="c"))
    _, items = stages.montage_index(cands, ctx, limit=12)
    assert {i["cand"].segments[0].episode for i in items} == set(range(1, 7))


# 7. hit@3 counts only labelled episodes in this run, per label
def test_reference_denominators():
    blocks = {"E001-S01-B001": {"start": 1.0, "end": 3.0, "episode": 1},
              "E001-S01-B009": {"start": 30.0, "end": 33.0, "episode": 1}}
    c = Candidate(promo_id="A", source="llm", segments=[Segment(episode=1, start=1, end=33)],
                  hook_block="E001-S01-B001", cliffhanger_block="E001-S01-B009", hook="h", cliffhanger="c")
    j = Judgement(promo_id="A", judge="x", samples=[], scores={}, spread={})
    rows = [RankedPromo(rank=1, promo_id="A", status="shipped", composite=4.0, candidate=c,
                        validation=Validation(promo_id="A", passed=True), judgement=j)]
    ref = {"episodes": {"1": {"hook": "E001-S01-B001"}, "70": {"hook": "E070-S01-B001", "cliffhanger": "x"}}}
    m = reference_metrics(rows, ref, blocks)
    assert m["hook_hit@3"] == 1.0 and m["cliffhanger_hit@3"] is None and m["labels_skipped_not_in_run"] == [70]


# 8. a corrupt cache file is a miss, not a crash
def test_corrupt_cache_is_a_miss(tmp_path, cfg):
    llm = LLM(cfg["llm"], tmp_path / "c", mock_fn=lambda *a: {"ok": 1})
    llm.complete_json("judge", {"clip": "x"})
    for f in (tmp_path / "c").glob("*.json"):
        f.write_text('{"data": {"ok"')
    assert llm.complete_json("judge", {"clip": "x"})["data"] == {"ok": 1}


# 10. new inputs: Mac junk files, BOM, nested zip folders
def test_input_edge_cases(tmp_path):
    d = write_pack(tmp_path / "pack", n=2)
    (d / "._episode-001-Episode 1000001.txt").write_bytes(b"\x00\x05\x16\x07binary")
    bom = tmp_path / "bom"
    bom.mkdir()
    (bom / "my show part one.txt").write_bytes("﻿".encode() + episode_text(1).encode())
    assert set(load_input(d)[0]) == {1, 2}
    assert parse_episode(bom / "my show part one.txt").episode == 1
    z = tmp_path / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Show A/episode-001-Episode 1.txt", episode_text(1))
        zf.writestr("Show A/episode-002-Episode 2.txt", episode_text(2))
        zf.writestr("__MACOSX/Show A/._episode-001-Episode 1.txt", "junk")
    eps, title, _ = load_input(z)
    assert set(eps) == {1, 2} and title == "Show A"


# 11. a filter that matches nothing is a clear error
def test_empty_filter(tmp_path, cfg):
    cfg["episode_filter"] = "70-80"
    with pytest.raises(ValueError, match="No episodes match"):
        run(write_pack(tmp_path / "pack"), tmp_path / "out", cfg, mode="baseline", progress=lambda *a, **k: None)


# 12. duplicate scene numbers keep block IDs unique
def test_duplicate_scene_numbers(tmp_path):
    txt = episode_text(1).replace("SCENE 01", "SCENE 01", 1)
    body = txt.split("--- SCRIPT END ---")[0]
    extra = body[body.index("SCENE 01"):].replace("00:00.00", "01:12.00", 1)
    f = tmp_path / "episode-001-Episode 1.txt"
    f.write_text(body + "\n" + extra + "\n--- SCRIPT END ---")
    ep = parse_episode(f)
    ids = [b.block_id for b in ep.blocks()]
    assert len(ids) == len(set(ids)) and any("renumbered" in i for i in ep.issues)


# 13. an empty first answer does not produce an empty assistant turn
def test_empty_answer_correction(tmp_path, cfg):
    seen = []

    class Fake(LLM):
        def _call_with_retries(self, messages, temperature):
            seen.append(messages)
            return ("" if len(seen) == 1 else '{"ok": 1}'), 1, 1

    c = dict(cfg["llm"], provider="anthropic", api_key="x")
    r = Fake(c, tmp_path / "c").complete_json("judge", {"clip": "x"})
    assert r["data"] == {"ok": 1}
    assert all(m["content"] for m in seen[1])
