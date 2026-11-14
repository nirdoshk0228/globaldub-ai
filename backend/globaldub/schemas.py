"""Core data model shared by the pipeline, API and worker.

One Episode holds the source-language segments once, plus one Track per target language.
The whole Episode is stored as a JSON document (Postgres JSONB in production).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]
LineStatus = Literal["pending", "auto_ok", "flagged", "approved", "edited", "kept"]
SEV_ORDER = {"low": 0, "medium": 1, "high": 2}


class Character(BaseModel):
    """A recurring character. Voices are pinned per language so a character sounds the same every episode."""

    name: str                                              # canonical Latin-script name, e.g. "Meera"
    source_aliases: list[str] = Field(default_factory=list)  # how the name appears in source, e.g. ["मीरा"]
    target_names: dict[str, str] = Field(default_factory=dict)  # lang -> name in that script, e.g. {"ta-IN": "மீரா"}
    gender: Literal["female", "male", "neutral"] = "neutral"
    age: Literal["child", "young", "adult", "senior"] = "adult"
    persona: str = ""
    voices: dict[str, str] = Field(default_factory=dict)   # "provider:lang" -> voice id
    pitch_offset_hz: int = 0                               # separates two characters sharing one stock voice


class GlossaryTerm(BaseModel):
    source: str                                            # e.g. "माँजी"
    target: dict[str, str] = Field(default_factory=dict)   # lang -> preferred rendering
    note: str = ""
    do_not_translate: bool = False


class Glossary(BaseModel):
    characters: list[Character] = Field(default_factory=list)
    terms: list[GlossaryTerm] = Field(default_factory=list)

    def character(self, name: str) -> Optional[Character]:
        for c in self.characters:
            if c.name.lower() == name.lower() or name in c.source_aliases:
                return c
        return None


class QAFlag(BaseModel):
    check: str            # omission | name_mismatch | number_mismatch | untranslated | literal_idiom |
                          # duration_overrun | glossary | script_mismatch | meaning_drift | tone_mismatch | register
    severity: Severity
    message: str
    suggestion: str = ""
    source: Literal["rule", "llm", "audio"] = "rule"


class Delivery(BaseModel):
    emotion: str = "neutral"
    intensity: float = 0.5
    pace: Literal["slow", "normal", "fast"] = "normal"
    note: str = ""


class Segment(BaseModel):
    """One source line."""

    id: str
    start: float
    end: float
    speaker: str
    text: str
    emotion: str = "neutral"
    reference: dict[str, str] = Field(default_factory=dict)  # optional gold lines (eval, mock mode)
    is_song: bool = False          # sung line: kept in the original voice (TTS can read lyrics, not sing them)
    audio_hint: str = ""           # how it was said, from the audio: "loud, fast" etc.

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class Line(BaseModel):
    """One localized line in one target language."""

    seg_id: str
    target_text: str = ""
    back_translation: str = ""      # meaning in English, so any reviewer can check any language
    adaptation_note: str = ""
    delivery: Delivery = Field(default_factory=Delivery)
    flags: list[QAFlag] = Field(default_factory=list)
    status: LineStatus = "pending"
    attempts: int = 0
    version: int = 0                # bumps on every text change; audio is cached per version
    tts_key: Optional[str] = None
    tts_version: int = -1
    tts_duration: Optional[float] = None
    stretch: float = 1.0

    @property
    def max_severity(self) -> Optional[str]:
        if not self.flags:
            return None
        return max(self.flags, key=lambda f: SEV_ORDER[f.severity]).severity


class StageMetric(BaseModel):
    stage: str
    seconds: float = 0.0
    cost_usd: float = 0.0
    units: dict[str, float] = Field(default_factory=dict)


class Track(BaseModel):
    lang: str
    status: Literal["queued", "running", "ready", "failed"] = "queued"
    stage: str = "queued"
    progress: float = 0.0
    lines: list[Line] = Field(default_factory=list)
    dub_key: Optional[str] = None
    srt_key: Optional[str] = None
    metrics: list[StageMetric] = Field(default_factory=list)
    error: Optional[str] = None

    def line(self, seg_id: str) -> Line:
        for ln in self.lines:
            if ln.seg_id == seg_id:
                return ln
        raise KeyError(seg_id)

    @property
    def cost_usd(self) -> float:
        return round(sum(m.cost_usd for m in self.metrics), 6)


class Episode(BaseModel):
    id: str
    title: str
    source_lang: str = "hi-IN"
    target_langs: list[str] = Field(default_factory=list)
    source_audio_key: Optional[str] = None
    # background music & effects ("M&E"): uploaded by the user or separated from the original mix
    bed_upload_key: Optional[str] = None
    bed_key: Optional[str] = None
    dialogue_key: Optional[str] = None   # dialogue-only stem (separated or voiced), used for ASR, clips and loudness
    background: str = "none"             # none | uploaded | separated:demucs | separated:center
    synopsis: str = ""
    segments: list[Segment] = Field(default_factory=list)
    glossary: Glossary = Field(default_factory=Glossary)
    tracks: dict[str, Track] = Field(default_factory=dict)
    ingest_metrics: list[StageMetric] = Field(default_factory=list)

    def seg(self, seg_id: str) -> Segment:
        for s in self.segments:
            if s.id == seg_id:
                return s
        raise KeyError(seg_id)

    @property
    def runtime_seconds(self) -> float:
        return max((s.end for s in self.segments), default=0.0)

    def slot(self, seg_id: str) -> float:
        """Time a dubbed line may use: until the next line starts (minus a breath), never less than its own length.
        Both the pre-synthesis estimate (QA) and the real audio fit (pipeline) use this, so they agree."""
        ids = [s.id for s in self.segments]
        i = ids.index(seg_id)
        seg = self.segments[i]
        nxt = self.segments[i + 1].start if i + 1 < len(self.segments) else seg.end + 1.5
        return max(seg.duration, nxt - seg.start - 0.08)
