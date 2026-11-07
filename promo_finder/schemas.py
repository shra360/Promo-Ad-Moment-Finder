"""Shared data contracts. Every stage reads and writes these, so a stage can be
rerun or replaced on its own. Times are seconds from the start of an episode."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DIMENSIONS = ("hook", "cliffhanger", "standalone", "coherence", "intensity", "curiosity")


def fmt_ts(t: float) -> str:
    """Seconds -> 'MM:SS.ff', the screenplay's own format."""
    m, s = divmod(round(t, 2), 60)
    return f"{int(m):02d}:{s:05.2f}"


class Segment(BaseModel):
    episode: int
    start: float
    end: float
    start_block: str | None = None   # block the model chose; times above are snapped
    end_block: str | None = None

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def label(self) -> str:
        return f"Ep {self.episode} {fmt_ts(self.start)}-{fmt_ts(self.end)}"


class Candidate(BaseModel):
    promo_id: str
    source: Literal["llm", "heuristic", "montage"]
    title: str = ""
    beat: str = ""                    # short label of the story event, used for dedupe
    segments: list[Segment]
    hook_block: str | None = None
    cliffhanger_block: str | None = None
    hook: str = ""
    cliffhanger: str = ""
    standalone_context: str = ""
    why: str = ""
    cold_open: bool = False           # segments intentionally out of story order
    repairs: list[str] = Field(default_factory=list)   # what code changed vs. the proposal

    @property
    def total(self) -> float:
        return round(sum(s.duration for s in self.segments), 2)

    @property
    def episodes(self) -> list[int]:
        return sorted({s.episode for s in self.segments})


class Check(BaseModel):
    rule: str
    detail: str


class Validation(BaseModel):
    promo_id: str
    passed: bool
    failures: list[Check] = Field(default_factory=list)
    warnings: list[Check] = Field(default_factory=list)


class JudgeSample(BaseModel):
    scores: dict[str, int]
    reasons: dict[str, str] = Field(default_factory=dict)
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    resolves_tension: bool = False
    needs_prior_context: bool = False
    safety_flags: list[str] = Field(default_factory=list)
    pitch: str = ""


class Judgement(BaseModel):
    promo_id: str
    judge: str                        # model name, or "heuristic"
    samples: list[JudgeSample]
    scores: dict[str, float]          # mean over samples
    spread: dict[str, float]          # max - min over samples (0 with one sample)
    evidence_ok: bool = True          # every cited block lies inside the clip
    notes: list[str] = Field(default_factory=list)


class RankedPromo(BaseModel):
    rank: int | None
    promo_id: str
    status: Literal["shipped", "below_gate", "not_selected", "duplicate", "invalid"]
    composite: float | None
    candidate: Candidate
    validation: Validation
    judgement: Judgement | None
    duplicate_of: str | None = None
    slate: bool = False
    review_flags: list[str] = Field(default_factory=list)
