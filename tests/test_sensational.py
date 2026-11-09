from promo_finder.config import load_config
from promo_finder.cuts import clean_cut_points
from promo_finder.llm import LLM
from promo_finder.pipeline import load_input, run_sensational
from promo_finder.sensational import llm_grades, pick

from .fixtures import write_pack


def test_keyword_mode_top3(tmp_path):
    pack = write_pack(tmp_path / "pack", n=3)
    cfg = load_config()
    res = run_sensational(pack, tmp_path / "out", cfg, mode="baseline", progress=lambda *a, **k: None)
    eps = load_input(pack)[0]
    assert 1 <= len(res["top"]) <= 3
    for w in res["top"]:
        assert 20 - 0.05 <= w["duration"] <= 60 + 0.05
        cuts = clean_cut_points(eps[w["episode"]])
        assert any(abs(w["start"] - c) < 0.05 for c in cuts) and any(abs(w["end"] - c) < 0.05 for c in cuts)
    assert len({w["episode"] for w in res["top"]}) == len(res["top"])       # one per episode by default
    for f in ("sensational.json", "sensational_top.csv", "sensational_per_episode.csv", "sensational.md"):
        assert (tmp_path / "out" / f).exists()


def test_pick_never_overlaps():
    c = [{"episode": 1, "start": 0, "end": 30, "duration": 30, "score": 9},
         {"episode": 1, "start": 10, "end": 40, "duration": 30, "score": 8},
         {"episode": 1, "start": 40, "end": 70, "duration": 30, "score": 7}]
    got = pick(c, 3, None)
    assert [x["start"] for x in got] == [0, 40]


def test_llm_grades_parse_and_ignore_bad_ids(tmp_path):
    pack = write_pack(tmp_path / "pack", n=1)
    ep = load_input(pack)[0][1]
    first = next(b for b in ep.blocks() if b.kind == "dialogue")
    answer = {"blocks": [{"block": first.block_id, "score": 8, "emotion": 3, "tags": ["slap"], "why": "slap"},
                         {"block": "E001-S01-B999", "score": 9}]}
    cfg = load_config()
    cfg["llm"]["provider"] = "mock"
    llm = LLM(cfg["llm"], tmp_path / "c", mock_fn=lambda name, v, s, r: answer if r else answer)
    g = llm_grades(llm, ep)
    assert g == {first.block_id: {"score": 8.0, "tags": ["slap"], "dims": {"emotion": 3}, "why": "slap"}}


def test_three_windows_per_episode(tmp_path):
    pack = write_pack(tmp_path / "pack", n=2)
    res = run_sensational(pack, tmp_path / "out", load_config(), mode="baseline", progress=lambda *a, **k: None)
    assert all(len(v) == 3 for v in res["per_episode"].values())


def test_quota_mid_run_keeps_results(tmp_path, monkeypatch):
    """Daily quota runs out after episode 1: the run still finishes, fallback grades fill the rest."""
    from promo_finder import sensational
    from promo_finder.llm import LLMError
    pack = write_pack(tmp_path / "pack", n=3)
    cfg = load_config()
    cfg["llm"].update(provider="gemini", api_key="fake")
    calls = {"n": 0}

    def fake_grades(llm, ep, aliases=None):
        calls["n"] += 1
        llm.stop_reason = "quota"
        raise LLMError("quota")

    monkeypatch.setattr(sensational, "llm_grades", fake_grades)
    monkeypatch.setattr(sensational, "finalize", lambda llm, ep, c, p: (_ for _ in ()).throw(LLMError("quota")))
    res = run_sensational(pack, tmp_path / "out", cfg, mode="llm", progress=lambda *a, **k: None)
    assert res["limit_reached"] is True
    assert len(res["grading_failed_episodes"]) == 3
    assert all(len(v) == 3 for v in res["per_episode"].values()) and res["top"]
