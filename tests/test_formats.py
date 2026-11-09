"""Format drift and silent-failure guards (found by eval/stress.py on the ReelBench pack)."""
import re

import pytest

from promo_finder.parse import parse_episode
from promo_finder.pipeline import load_input

from .fixtures import episode_text, write_pack


def sig(ep):
    return [(b.kind, b.start, b.end, b.speaker) for b in ep.blocks()]


VARIANTS = {
    "crlf": lambda t: t.replace("\n", "\r\n"),
    "no_bold": lambda t: re.sub(r"^\*\*(.+?):\*\*\s*$", r"\1:", t, flags=re.M),
    "no_parens": lambda t: re.sub(r"Start: \(([\d:.]+)\) \| End: \(([\d:.]+)\)", r"Start: \1 | End: \2", t),
    "hours": lambda t: re.sub(r"\((\d{2}:\d{2}\.\d{2})\)", r"(00:\1)", t),
    "en_dash": lambda t: t.replace(") | End: (", ") – End: ("),
    "lower_labels": lambda t: re.sub(r"\*\*(CAMERA|ACTION|SOUND FX):\*\*", lambda m: f"**{m.group(1).title()}:**", t),
    "bom": lambda t: "﻿" + t,
}


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_variant_parses_identically(tmp_path, name):
    a = tmp_path / "episode-004-Episode 1.txt"
    b = tmp_path / "v" / "episode-004-Episode 1.txt"
    b.parent.mkdir()
    txt = episode_text(4)
    a.write_text(txt, encoding="utf-8")
    b.write_text(VARIANTS[name](txt), encoding="utf-8", newline="")
    ea, eb = parse_episode(a), parse_episode(b)
    assert sig(ea) == sig(eb) and sig(ea)
    assert not [i for i in eb.issues if "UNUSABLE" in i]


def test_plain_labels_do_not_eat_scene_heading(tmp_path):
    f = tmp_path / "episode-001.txt"
    f.write_text(VARIANTS["no_bold"](episode_text(1)), encoding="utf-8")
    ep = parse_episode(f)
    assert ep.scenes[0].heading.startswith("INT.") and ep.episode == 1


def test_untimed_episode_is_reported_not_used(tmp_path):
    d = write_pack(tmp_path / "pack", n=2)
    bad = next(d.glob("episode-002-*"))
    bad.write_text(re.sub(r"Start: .*", "Start: soon | End: later", bad.read_text(encoding="utf-8")), encoding="utf-8")
    eps, _, issues = load_input(d)
    assert set(eps) == {1} and any("UNUSABLE" in i for i in issues)
    bad1 = next(d.glob("episode-001-*"))
    bad1.write_text(re.sub(r"Start: .*", "Start: soon | End: later", bad1.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ValueError, match="No usable episode"):
        load_input(d)
