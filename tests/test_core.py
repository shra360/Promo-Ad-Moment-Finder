import json

import pytest

from promo_finder.assemble import build
from promo_finder.config import load_config
from promo_finder.cuts import clean_cut_points, windows
from promo_finder.llm import LLM, extract_json
from promo_finder.parse import parse_episode
from promo_finder.pipeline import load_input, run
from promo_finder.schemas import Candidate, Segment
from promo_finder.validate import Context, validate

from .fixtures import write_pack


@pytest.fixture()
def pack(tmp_path):
    return write_pack(tmp_path / "pack")


@pytest.fixture()
def cfg():
    c = load_config()
    c["llm"]["provider"] = "mock"
    return c


@pytest.fixture()
def ctx(pack):
    eps, _, _ = load_input(pack)
    return Context(eps)


def rules(v):
    return {f.rule for f in v.failures}


# ---------------- parsing
def test_parse_structure_and_labels(pack):
    ep = parse_episode(next(pack.glob("episode-001-*")))
    assert ep.episode == 1 and not ep.issues
    kinds = {b.kind for b in ep.blocks()}
    assert kinds == {"camera", "action", "dialogue", "sfx"}
    old = [b for b in ep.blocks() if b.speaker == "OLD MAN"]
    assert old and all(b.voice == "off_screen" for b in old)          # both '(O.S.)' positions
    assert all(b.block_id.startswith("E001-S01-B") for b in ep.blocks())


def test_parse_uppercase_scene_keys(pack):
    ep = parse_episode(next(pack.glob("episode-002-*")))
    assert ep.scenes[0].start == 0 and ep.scenes[0].end == 72.0 and not ep.issues


def test_header_number_used_when_filename_differs(tmp_path, pack):
    src = next(pack.glob("episode-003-*")).read_text()
    f = tmp_path / "some_new_show_part_three.txt"
    f.write_text(src)
    assert parse_episode(f).episode == 3


# ---------------- cut points
def test_clean_cuts_avoid_spoken_lines(ctx):
    ep = ctx.episodes[1]
    cuts = clean_cut_points(ep)
    lines = [(b.start, b.end) for b in ep.blocks() if b.kind == "dialogue"]
    assert cuts and not any(s + 0.05 < c < e - 0.05 for c in cuts for s, e in lines)
    assert 4.0 not in cuts               # shot change at 4.0 s is mid-line (line runs 1.0-5.0)
    for a, b in windows(cuts):
        assert 20 - 0.05 <= b - a <= 60 + 0.05


# ---------------- validation rules
def good(ctx, **kw) -> Candidate:
    bm = ctx.episodes[1].block_map()
    hook = next(b for b in bm.values() if b.kind == "dialogue" and b.start >= 8)
    cliff = next(b for b in reversed(list(bm.values())) if b.kind == "dialogue" and b.end <= hook.start + 40)
    seg = Segment(episode=1, start=hook.start, end=cliff.end, start_block=hook.block_id, end_block=cliff.block_id)
    d = dict(promo_id="T1", source="llm", segments=[seg], hook_block=hook.block_id,
             cliffhanger_block=cliff.block_id, hook="h", cliffhanger="c")
    d.update(kw)
    return Candidate(**d)


def test_valid_candidate_passes(ctx, cfg):
    v = validate(good(ctx), ctx, cfg)
    assert v.passed, v.failures


def test_too_long_fails_V7(ctx, cfg):
    c = good(ctx)
    c.segments[0].end = clean_cut_points(ctx.episodes[1])[-1]
    c.segments[0].start = 1.0
    c.hook_block = next(b.block_id for b in ctx.episodes[1].blocks() if b.start == 1.0)
    assert "V7" in rules(validate(c, ctx, cfg))


def test_cut_inside_line_fails_V5(ctx, cfg):
    c = good(ctx)
    c.segments[0].end -= 1.0               # now ends mid-line
    assert "V5" in rules(validate(c, ctx, cfg))


def test_late_hook_fails_V10(ctx, cfg):
    c = good(ctx)
    later = [b for b in ctx.episodes[1].blocks() if b.start > c.segments[0].start + 5]
    c.hook_block = later[0].block_id
    assert "V10" in rules(validate(c, ctx, cfg))


def test_invented_block_fails_V2(ctx, cfg):
    c = good(ctx, cliffhanger_block="E001-S01-B999")
    assert "V2" in rules(validate(c, ctx, cfg))


def test_overlap_and_order(ctx, cfg):
    c = good(ctx)
    s = c.segments[0]
    c.segments = [s, Segment(episode=1, start=s.start, end=s.start + 8)]
    assert "V8" in rules(validate(c, ctx, cfg))
    a = Segment(episode=1, start=s.end - 10, end=s.end)
    b = Segment(episode=1, start=s.start, end=s.start + 12)
    c.segments = [a, b]
    assert "V9" in rules(validate(c, ctx, cfg))
    c.cold_open = True
    assert "V9" not in rules(validate(c, ctx, cfg))


# ---------------- assembly
def test_build_snaps_and_jump_cuts(ctx, cfg):
    bm = ctx.episodes[1].block_map()
    ids = list(bm)
    first_line = next(b for b in bm.values() if b.kind == "dialogue")
    prop = {"segments": [{"start_block": first_line.block_id, "end_block": ids[-1]}],
            "hook_block": first_line.block_id, "cliffhanger_block": ids[-1], "hook": "h", "cliffhanger": "c"}
    c, why = build(prop, "T2", "llm", ctx, cfg)
    assert c and not why
    assert len(c.segments) == 2 and c.total <= 60          # 71 s span was jump-cut
    assert validate(c, ctx, cfg).passed
    c2, why2 = build({"segments": [{"start_block": "E001-S01-B999", "end_block": ids[-1]}]}, "T3", "llm", ctx, cfg)
    assert c2 is None and "invented" in why2


# ---------------- model layer
def test_extract_json_tolerates_fences_and_commas():
    assert extract_json('```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}
    assert extract_json('Sure! {"x": 1} done') == {"x": 1}


def test_correction_round_and_cache(tmp_path, cfg):
    calls = []

    def fake(name, v, sample, round_):
        calls.append(round_)
        return {"scores": {"hook": 9}} if round_ == 0 else {"scores": {"hook": 4}}

    llm = LLM(cfg["llm"], tmp_path / "cache", tmp_path / "log.jsonl", mock_fn=fake)
    chk = lambda d: [] if 1 <= d["scores"]["hook"] <= 5 else ["hook must be 1-5"]
    r = llm.complete_json("judge", {"clip": "x"}, check=chk)
    assert r["data"]["scores"]["hook"] == 4 and len(r["attempts"]) == 2 and calls == [0, 1]
    r2 = llm.complete_json("judge", {"clip": "x"}, check=chk)
    assert r2["data"] == r["data"] and calls == [0, 1]      # served from cache
    log = [json.loads(l) for l in (tmp_path / "log.jsonl").read_text().splitlines()]
    assert [x["cached"] for x in log] == [False, True]


# ---------------- end to end
@pytest.mark.parametrize("mode", ["baseline", "llm"])
def test_pipeline_end_to_end(tmp_path, pack, cfg, mode):
    cfg["cache_dir"] = str(tmp_path / "cache")
    out = tmp_path / f"run_{mode}"
    m = run(pack, out, cfg, mode=mode, progress=lambda *a, **k: None)
    for f in ("promos_ranked.json", "promos_ranked.csv", "report.md", "eval_report.md", "run_manifest.json",
              "bible.json", "candidates.json", "judgements.json"):
        assert (out / f).exists(), f
    rows = json.loads((out / "promos_ranked.json").read_text())
    ctx = Context(load_input(pack)[0])
    for r in rows:
        if r["status"] == "shipped":
            c = Candidate.model_validate(r["candidate"])
            assert validate(c, ctx, cfg).passed
            assert 20 <= c.total <= 60
    assert "api_key" not in json.dumps(m["config"]["llm"]).replace("api_key_set", "")
