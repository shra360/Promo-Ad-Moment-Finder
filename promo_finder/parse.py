"""Deterministic screenplay parser (Agent 1: ingestion).

Turns one episode .txt into Episode -> Scene -> Block records. Never invents
timestamps or rewrites text: every block keeps its raw source lines and the
line number it came from, and anything that does not match the expected shape
is recorded as an issue instead of being silently fixed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

# A time is [HH:]MM:SS[.ff]. Block times: "Start: (MM:SS.ff) | End: (MM:SS.ff)", with the parentheses optional
# and "|", "-", en/em dash, "," or ";" accepted as the separator.
TS = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?"
RE_TS = re.compile(rf"^{TS}$")
RE_BLOCK_TIME = re.compile(
    rf"^Start:\s*\(?\s*{TS}\s*\)?\s*[|\-\u2013\u2014,;]?\s*End:\s*\(?\s*{TS}\s*\)?\s*$", re.I)
# A track or speaker label on its own line: "**CAMERA:**" or, without the bold markers, "CAMERA:".
# The unbolded form must be upper case before any parenthesis, so "Scene heading: ..." never matches.
RE_LABEL = re.compile(r"^\*\*(.+?):\*\*\s*$")
RE_LABEL_PLAIN = re.compile(r"^([A-Z0-9][A-Z0-9 .'\-&/]*(?:\s*\([^)]*\))*)\s*:\s*$")
RE_SCENE = re.compile(r"^SCENE\s+(\d+)\s*$")
RE_FILE = re.compile(r"episode-(\d{1,4})(?:-Episode\s+(\d+))?\.txt$", re.I)   # episode-001.txt or episode-001-Episode 123.txt

NON_DIALOGUE = {"CAMERA", "ACTION", "SOUND FX", "SFX", "MUSIC", "TRANSITION", "TEXT", "TITLE", "SUPER"}


def _secs(h, mm, ss, frac) -> float:
    return (int(h) * 3600 if h else 0) + int(mm) * 60 + int(ss) + (float(f"0.{frac}") if frac else 0.0)


def to_seconds(m_or_s: str) -> float | None:
    m = RE_TS.match(m_or_s.strip().strip("()"))
    return _secs(*m.groups()) if m else None


def _group_secs(groups, offset: int) -> float:
    return _secs(*groups[offset:offset + 4])


@dataclass
class Block:
    block_id: str
    kind: str                 # camera | action | sfx | dialogue | other
    label: str                # raw label, e.g. "LADY JULIANA (Confused, Breathless)"
    speaker: str | None       # "LADY JULIANA" for dialogue
    delivery: list[str]       # parentheticals, e.g. ["Confused, Breathless"]
    voice: str | None         # off_screen | voice_over | None (on-screen)
    start: float | None
    end: float | None
    text: str
    line_no: int


@dataclass
class Scene:
    scene_no: int
    heading: str | None
    start: float | None
    end: float | None
    line_no: int
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Episode:
    episode: int | None
    source_file: str
    file_episode_id: str | None
    header_episode: str | None
    logline: str | None
    continuity_notes: str | None
    scenes: list[Scene] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    has_start_marker: bool = False
    has_end_marker: bool = False

    def blocks(self):
        for s in self.scenes:
            yield from s.blocks

    def block_map(self) -> dict[str, "Block"]:
        return {b.block_id: b for b in self.blocks()}

    @property
    def duration(self) -> float:
        ends = [s.end for s in self.scenes if s.end is not None]
        ends += [b.end for b in self.blocks() if b.end is not None]
        return max(ends) if ends else 0.0

    def to_dict(self):
        return asdict(self)


VOICE_MODES = {"O.S.": "off_screen", "OFF-SCREEN": "off_screen", "OFF SCREEN": "off_screen",
               "V.O.": "voice_over", "VO": "voice_over"}


def _classify(label: str):
    """Split '**MATRIARCH (O.S.) (Firm)**' / 'PRUDENCE (Solemn, O.S.)' into
    kind, speaker, delivery cues and voice mode (seen in the pack in both forms)."""
    base = re.split(r"\s*\(", label, maxsplit=1)[0].strip()
    parens = re.findall(r"\(([^)]*)\)", label)
    delivery, voice = [], None
    for p in parens:
        kept = []
        for tok in (t.strip() for t in p.split(",")):
            mode = VOICE_MODES.get(tok.upper())
            if mode:
                voice = mode
            elif tok:
                kept.append(tok)
        if kept:
            delivery.append(", ".join(kept))
    up = base.upper()
    if up == "CAMERA":
        return "camera", None, delivery, voice
    if up == "ACTION":
        return "action", None, delivery, voice
    if up in {"SOUND FX", "SFX", "SOUND"}:
        return "sfx", None, delivery, voice
    if up in NON_DIALOGUE:
        return "other", None, delivery, voice
    return "dialogue", base, delivery, voice


def parse_episode(path: str | Path) -> Episode:
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    fm = RE_FILE.search(path.name)
    ep = Episode(
        episode=int(fm.group(1)) if fm else None,
        source_file=path.name,
        file_episode_id=fm.group(2) if fm else None,
        header_episode=None, logline=None, continuity_notes=None,
    )
    # a non-standard file name is fine: the number then comes from the EPISODE NUMBER header

    scene: Scene | None = None
    pending: dict | None = None   # block being assembled

    def flush():
        nonlocal pending
        if pending is None:
            return
        if scene is None:
            ep.issues.append(f"L{pending['line_no']}: block outside any scene")
        else:
            kind, speaker, delivery, voice = _classify(pending["label"])
            idx = len(scene.blocks) + 1
            text = "\n".join(pending["text"]).strip()
            b = Block(
                block_id=f"E{ep.episode or 0:03d}-S{scene.scene_no:02d}-B{idx:03d}",
                kind=kind, label=pending["label"], speaker=speaker, delivery=delivery,
                voice=voice, start=pending["start"], end=pending["end"], text=text,
                line_no=pending["line_no"],
            )
            if b.start is None or b.end is None:
                ep.issues.append(f"{b.block_id} L{b.line_no}: missing/malformed Start|End line")
            if not text:
                ep.issues.append(f"{b.block_id} L{b.line_no}: empty block text")
            scene.blocks.append(b)
        pending = None

    for i, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        s = line.strip()
        if s.upper().startswith("EPISODE NUMBER:"):
            ep.header_episode = s.split(":", 1)[1].strip()
            if ep.episode is None:   # new inputs may not follow the file naming
                hn = re.search(r"(\d+)", ep.header_episode)
                ep.episode = int(hn.group(1)) if hn else None
            continue
        if s.upper().startswith("EPISODE LOGLINE:"):
            ep.logline = s.split(":", 1)[1].strip()
            continue
        if s.upper().startswith("CONTINUITY NOTES:"):
            ep.continuity_notes = s.split(":", 1)[1].strip()
            continue
        if s == "--- SCRIPT START ---":
            ep.has_start_marker = True
            continue
        if s == "--- SCRIPT END ---":
            ep.has_end_marker = True
            flush()
            continue
        m = RE_SCENE.match(s)
        if m:
            flush()
            no = int(m.group(1))
            if no != len(ep.scenes) + 1:   # keeps block IDs unique even if the script repeats a number
                ep.issues.append(f"L{i}: SCENE {no} renumbered to {len(ep.scenes) + 1}")
                no = len(ep.scenes) + 1
            scene = Scene(scene_no=no, heading=None, start=None, end=None, line_no=i)
            ep.scenes.append(scene)
            continue
        if scene is not None and pending is None:
            low = s.lower()   # ep 6 writes these keys in capitals
            if low.startswith("scene heading:"):
                scene.heading = s.split(":", 1)[1].strip()
                continue
            if low.startswith("scene start:"):
                scene.start = to_seconds(s.split(":", 1)[1])
                if scene.start is None:
                    ep.issues.append(f"L{i}: malformed scene start '{s}'")
                continue
            if low.startswith("scene end:"):
                scene.end = to_seconds(s.split(":", 1)[1])
                if scene.end is None:
                    ep.issues.append(f"L{i}: malformed scene end '{s}'")
                continue
            if low == "actions and dialogue:":
                continue
        m = RE_LABEL.match(s) or (RE_LABEL_PLAIN.match(s) if scene is not None else None)
        if m:
            flush()
            pending = {"label": m.group(1).strip(), "start": None, "end": None, "text": [], "line_no": i}
            continue
        if pending is not None:
            if pending["start"] is None and s.lower().startswith("start:"):
                mt = RE_BLOCK_TIME.match(s)
                if mt:
                    g = mt.groups()
                    pending["start"], pending["end"] = _group_secs(g, 0), _group_secs(g, 4)
                else:
                    ep.issues.append(f"L{i}: unparseable time line '{s}'")
                continue
            if s:
                pending["text"].append(s)
            continue
        if s:
            ep.issues.append(f"L{i}: unrecognised line outside a block: '{s[:60]}'")
    flush()

    timed = sum(1 for b in ep.blocks() if b.start is not None and b.end is not None)
    total = sum(1 for _ in ep.blocks())
    if total == 0:
        ep.issues.append("UNUSABLE: no blocks found (check the **LABEL:** lines)")
    elif timed < 0.8 * total:
        ep.issues.append(f"UNUSABLE: only {timed} of {total} blocks have readable Start/End times")
    if not ep.has_start_marker:
        ep.issues.append("missing '--- SCRIPT START ---'")
    if not ep.has_end_marker:
        ep.issues.append("missing '--- SCRIPT END ---'")
    if ep.header_episode and ep.episode is not None:
        hn = re.search(r"(\d+)", ep.header_episode)
        if not hn or int(hn.group(1)) != ep.episode:
            ep.issues.append(f"header episode '{ep.header_episode}' != filename episode {ep.episode}")
    return ep


def episode_from_dict(d: dict) -> Episode:
    """Rebuild an Episode from its saved parsed/episode-NNN.json."""
    scenes = [Scene(scene_no=s["scene_no"], heading=s["heading"], start=s["start"], end=s["end"],
                    line_no=s["line_no"], blocks=[Block(**b) for b in s["blocks"]]) for s in d["scenes"]]
    return Episode(episode=d["episode"], source_file=d["source_file"], file_episode_id=d["file_episode_id"],
                   header_episode=d["header_episode"], logline=d["logline"], continuity_notes=d["continuity_notes"],
                   scenes=scenes, issues=d["issues"], has_start_marker=d["has_start_marker"],
                   has_end_marker=d["has_end_marker"])


def load_parsed(folder: str | Path) -> dict[int, Episode]:
    import json
    eps = [episode_from_dict(json.loads(f.read_text(encoding="utf-8"))) for f in sorted(Path(folder).glob("episode-*.json"))]
    return {e.episode: e for e in eps}
