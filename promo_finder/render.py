"""Turn parsed episodes and clips into the plain-text views that prompts and the
app use. One line per block, always carrying its block ID and time."""
from __future__ import annotations

from .parse import Block, Episode
from .schemas import Candidate, fmt_ts

EPS = 0.05


def speaker_name(b: Block, aliases: dict[str, str] | None) -> str:
    if b.kind != "dialogue":
        return {"camera": "CAMERA", "action": "ACTION", "sfx": "SOUND FX"}.get(b.kind, b.label.upper())
    name = (aliases or {}).get(b.speaker or "", None)
    base = f"{name} [{b.speaker}]" if name and name.upper() != (b.speaker or "").upper() else (b.speaker or "?")
    extra = []
    if b.voice:
        extra.append(b.voice.replace("_", "-"))
    if b.delivery:
        extra.extend(b.delivery)
    return f"{base} ({'; '.join(extra)})" if extra else base


def block_line(b: Block, aliases=None, rel_to: float | None = None) -> str:
    if rel_to is None:
        t = f"{fmt_ts(b.start)}-{fmt_ts(b.end)}"
    else:
        t = f"+{b.start - rel_to:.1f}s"
    text = " ".join(b.text.split())
    return f"{b.block_id} | {t} | {speaker_name(b, aliases)}: {text}"


def sorted_blocks(ep: Episode) -> list[Block]:
    order = {"camera": 0, "sfx": 1, "action": 2, "dialogue": 3, "other": 4}
    bs = [b for b in ep.blocks() if b.start is not None and b.end is not None]
    return sorted(bs, key=lambda b: (b.start, order.get(b.kind, 9)))


def episode_text(ep: Episode, aliases: dict[str, str] | None = None) -> str:
    head = [f"EPISODE {ep.episode} (runs {fmt_ts(ep.duration)})",
            f"Logline: {ep.logline or '-'}",
            f"Continuity notes: {ep.continuity_notes or '-'}", ""]
    lines = []
    scene_no = None
    for b in sorted_blocks(ep):
        sn = b.block_id.split("-")[1]
        if sn != scene_no:
            scene_no = sn
            sc = next((s for s in ep.scenes if f"S{s.scene_no:02d}" == sn), None)
            if sc:
                lines.append(f"--- SCENE {sc.scene_no}: {sc.heading or ''} "
                             f"[{fmt_ts(sc.start or 0)}-{fmt_ts(sc.end or 0)}]")
        lines.append(block_line(b, aliases))
    return "\n".join(head + lines)


def clip_blocks(ep: Episode, start: float, end: float) -> list[Block]:
    """Blocks that play inside [start, end]: those starting in the window."""
    return [b for b in sorted_blocks(ep) if start - EPS <= b.start < end - EPS]


def clip_text(cand: Candidate, episodes: dict[int, Episode], aliases_by_ep: dict | None = None,
              blind: bool = True) -> str:
    """The clip as a viewer would get it, segment by segment. Times are relative
    to the clip start so the judge can check the first 3 seconds."""
    out, offset = [], 0.0
    for i, seg in enumerate(cand.segments, 1):
        ep = episodes[seg.episode]
        al = (aliases_by_ep or {}).get(str(seg.episode)) or (aliases_by_ep or {}).get(seg.episode)
        hdr = f"=== SEGMENT {i} ({seg.duration:.1f}s)"
        if not blind:
            hdr += f" from episode {seg.episode} {fmt_ts(seg.start)}-{fmt_ts(seg.end)}"
        out.append(hdr + " ===")
        for b in clip_blocks(ep, seg.start, seg.end):
            rel = offset + (b.start - seg.start)
            out.append(block_line(b, al, rel_to=b.start - rel))
        offset += seg.duration
    return "\n".join(out)
