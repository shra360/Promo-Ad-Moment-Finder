"""No-LLM baseline. Scores blocks from delivery cues, charged words and
punctuation, pairs strong openings with strong endings 20-60 s later, and
judges clips with the same 1-5 rubric. It runs anywhere, costs nothing, and is
the yardstick the LLM pipeline has to beat."""
from __future__ import annotations

import re

from .parse import Block, Episode
from .render import clip_blocks, sorted_blocks
from .schemas import Candidate, JudgeSample

CUES = {
    3: ["screaming", "shrieking", "hysterical", "enraged", "horrified", "terrified", "deadly", "murderous",
        "wailing", "furious", "outraged", "devastated", "threatening", "menacing", "chilling"],
    2: ["shocked", "gasping", "sobbing", "crying", "desperate", "pleading", "sinister", "cruel", "gloating",
        "mocking", "venomous", "vicious", "accusing", "defiant", "ruthless", "triumphant", "scornful",
        "panicked", "begging", "betrayed", "heartbroken", "cold", "icy", "seething", "trembling"],
    1: ["firm", "stern", "urgent", "bitter", "sharp", "smug", "taunting", "resolute", "dismissive", "harsh"],
}
WORDS = {
    3: ["slap", "kill", "murder", "dead", "dying", "hang", "noose", "gallows", "poison", "witch", "blood",
        "explosion", "explodes", "arrest", "traitor", "imposter", "pregnant", "betray"],
    2: ["marry", "married", "wedding", "divorce", "secret", "truth", "lie", "liar", "heir", "duke", "duchess",
        "prison", "burn", "fire", "scream", "gasp", "collapse", "knife", "sword", "threat", "ruin", "destroy",
        "revenge", "never", "how dare", "shackle", "chains", "guards", "witness"],
    1: ["please", "why", "who", "what have", "father", "mother", "sister", "husband", "wife"],
}
ACTION_VERBS = ["slaps", "strikes", "grabs", "shoves", "collapses", "lunges", "seizes", "bursts", "smashes",
                "drops to", "stumbles", "explod", "shatter", "slam", "falls"]
ENDING_MARKS = ["cliffhanger", "cut to black", "cuts to black", "smash cut", "freeze"]


def block_score(b: Block) -> float:
    text = b.text.lower()
    cues = " ".join(b.delivery).lower()
    s = 0.0
    for w, terms in CUES.items():
        s += w * sum(1 for t in terms if t in cues)
    for w, terms in WORDS.items():
        s += 0.7 * w * sum(1 for t in terms if re.search(rf"\b{re.escape(t)}", text))
    if b.kind == "dialogue":
        s += 0.6 * text.count("!") + 0.4 * ("?" in text)
    if b.kind in ("action", "camera"):
        s += 1.2 * sum(1 for v in ACTION_VERBS if v in text)
    if b.kind == "sfx":
        s += 1.0 + 1.2 * sum(1 for v in ("slam", "crash", "gasp", "scream", "explos", "shatter", "slap") if v in text)
    return round(s, 2)


def _ends_open(b: Block) -> bool:
    t = b.text.lower()
    return "?" in t or any(m in t for m in ENDING_MARKS) or any(
        w in t for w in ("will", "never", "tomorrow", "unless", "or else", "before", "until"))


def propose(ep: Episode, n: int = 4, min_total: float = 20, max_total: float = 60) -> list[dict]:
    blocks = [b for b in sorted_blocks(ep) if b.kind != "camera" or block_score(b) > 0]
    scored = [(block_score(b), b) for b in blocks]
    hooks = sorted([x for x in scored if x[0] >= 1.5], key=lambda x: -x[0])[:25]
    cliffs = [x for x in scored if x[0] >= 1.0 and (x[1].kind != "camera" or _ends_open(x[1]))]
    pairs = []
    for hs, h in hooks:
        for cs, c in cliffs:
            span = c.end - h.start
            if min_total + 2 <= span <= max_total - 5:
                bonus = 1.5 * _ends_open(c) + (1.0 if ep.duration - c.end > 10 else 0)
                pairs.append((1.2 * hs + cs + bonus, h, c))
    pairs.sort(key=lambda x: -x[0])
    chosen = []
    for sc, h, c in pairs:
        if any(not (c.end <= h2.start or h.start >= c2.end) and
               (min(c.end, c2.end) - max(h.start, h2.start)) > 0.5 * (c.end - h.start) for _, h2, c2 in chosen):
            continue
        chosen.append((sc, h, c))
        if len(chosen) == n:
            break
    out = []
    for sc, h, c in chosen:
        out.append({
            "title": f"Ep {ep.episode}: {_short(h.text, 6)}",
            "beat": _short(c.text, 6),
            "segments": [{"start_block": h.block_id, "end_block": c.block_id}],
            "hook_block": h.block_id, "cliffhanger_block": c.block_id,
            "hook": _short(h.text, 18), "cliffhanger": _short(c.text, 18),
            "standalone_context": "not assessed by the baseline",
            "why": f"keyword/cue score {sc:.1f}",
        })
    return out


def _short(t: str, words: int) -> str:
    w = " ".join(t.split()).strip('"').split()
    return " ".join(w[:words]) + ("..." if len(w) > words else "")


def _to5(x: float, cuts=(1, 2, 3.5, 5)) -> int:
    return 1 + sum(x >= c for c in cuts)


def judge(cand: Candidate, episodes: dict[int, Episode]) -> JudgeSample:
    first, last = cand.segments[0], cand.segments[-1]
    head = [b for b in clip_blocks(episodes[first.episode], first.start, first.end) if b.start <= first.start + 3]
    tail = [b for b in clip_blocks(episodes[last.episode], last.start, last.end) if b.start >= last.end - 6]
    allb = [b for s in cand.segments for b in clip_blocks(episodes[s.episode], s.start, s.end)]
    hook = _to5(max([block_score(b) for b in head] or [0]))
    cliff_raw = max([block_score(b) for b in tail] or [0]) + 1.5 * any(_ends_open(b) for b in tail)
    cliff = _to5(cliff_raw)
    speakers = {b.speaker for b in allb if b.kind == "dialogue"}
    nlines = sum(b.kind == "dialogue" for b in allb)
    standalone = 3 + (nlines >= 5 and len(speakers) >= 2) - (first.start < 10)
    intensity = _to5(sum(block_score(b) for b in allb) / max(len(allb), 1), cuts=(0.5, 1, 1.8, 2.8))
    coherence = 4 if len(cand.segments) == 1 else 3
    curiosity = round((hook + cliff) / 2)
    return JudgeSample(
        scores={"hook": hook, "cliffhanger": cliff, "standalone": max(1, min(5, standalone)),
                "coherence": coherence, "intensity": intensity, "curiosity": curiosity},
        reasons={"hook": "keyword and delivery-cue score of blocks in the first 3 s",
                 "cliffhanger": "cue score of the last 6 s, plus an open question or threat",
                 "standalone": "line and speaker count; minus one if it opens on a recap"},
        evidence={"hook": [b.block_id for b in head[:2]], "cliffhanger": [b.block_id for b in tail[-2:]]},
        pitch="baseline (no LLM)")
