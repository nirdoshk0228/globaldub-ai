"""Helpers to create Episodes from uploads and to summarise them."""
from __future__ import annotations

import uuid
from pathlib import Path

from .providers.asr import load_script
from .schemas import Character, Episode, Glossary, GlossaryTerm, Track

SCRIPT_SUFFIXES = {".json", ".srt", ".txt"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".mp4", ".webm"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def episode_from_script(path: Path, target_langs: list[str], title: str | None = None,
                        source_lang: str = "hi-IN", ep_id: str | None = None) -> Episode:
    segs, meta = load_script(path, source_lang)
    glossary = Glossary(
        characters=[Character(**c) for c in meta.get("characters", [])],
        terms=[GlossaryTerm(**t) for t in meta.get("glossary", [])],
    )
    return Episode(
        id=ep_id or new_id(),
        title=title or meta.get("title") or path.stem,
        source_lang=meta.get("source_lang", source_lang),
        target_langs=target_langs,
        synopsis=meta.get("synopsis", ""),
        segments=segs,
        glossary=glossary,
        tracks={lang: Track(lang=lang) for lang in target_langs},
    )


def episode_from_audio(audio_key: str, target_langs: list[str], title: str,
                       source_lang: str = "hi-IN", ep_id: str | None = None) -> Episode:
    return Episode(
        id=ep_id or new_id(),
        title=title,
        source_lang=source_lang,
        target_langs=target_langs,
        source_audio_key=audio_key,
        tracks={lang: Track(lang=lang) for lang in target_langs},
    )


def track_summary(ep: Episode, lang: str) -> dict:
    t = ep.tracks[lang]
    lines = t.lines
    n = max(1, len(lines))
    flagged = [ln for ln in lines if ln.status == "flagged"]
    by_check: dict[str, int] = {}
    for ln in lines:
        for f in ln.flags:
            if f.severity in ("medium", "high"):
                by_check[f.check] = by_check.get(f.check, 0) + 1
    minutes = max(ep.runtime_seconds / 60, 1e-6)
    ingest_cost = sum(m.cost_usd for m in ep.ingest_metrics) / max(1, len(ep.target_langs))
    return {
        "lang": lang,
        "status": t.status,
        "stage": t.stage,
        "progress": t.progress,
        "error": t.error,
        "lines": len(lines),
        "auto_ok": sum(ln.status == "auto_ok" for ln in lines),
        "flagged": len(flagged),
        "approved": sum(ln.status in ("approved", "edited") for ln in lines),
        "kept_original": sum(ln.status == "kept" for ln in lines),
        "needs_review_pct": round(100 * len(flagged) / n, 1),
        "flags_by_check": by_check,
        "stretched_lines": sum(ln.stretch > 1.0 for ln in lines),
        "cost_usd": round(t.cost_usd + ingest_cost, 6),
        "cost_per_minute_usd": round((t.cost_usd + ingest_cost) / minutes, 6),
        "processing_seconds": round(sum(m.seconds for m in t.metrics), 2),
        "metrics": [m.model_dump() for m in t.metrics],
    }
