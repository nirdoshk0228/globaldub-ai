"""Voice Director: pins one stock voice per character per language, and keeps characters distinguishable."""
from __future__ import annotations

from ..langpacks import load_pack
from ..schemas import Character, Episode

AGE_ORDER = ["child", "young", "adult", "senior"]


def _score(c: Character, v: dict) -> float:
    if c.gender != "neutral" and v.get("gender") != c.gender:
        return -10
    a = AGE_ORDER.index(c.age) if c.age in AGE_ORDER else 2
    b = AGE_ORDER.index(v.get("age", "adult")) if v.get("age", "adult") in AGE_ORDER else 2
    return 3 - abs(a - b)


def assign_voices(ep: Episode, provider: str, langs: list[str]) -> None:
    """Deterministic, sticky assignment: a voice once chosen for a character is never changed by the pipeline."""
    for lang in langs:
        catalog = load_pack(lang).voice_catalog(provider)
        if not catalog:
            raise RuntimeError(f"No {provider} voices configured for {lang}")
        key = f"{provider}:{lang}"
        used: dict[str, int] = {}
        for c in ep.glossary.characters:
            if key in c.voices:
                used[c.voices[key]] = used.get(c.voices[key], 0) + 1
        for c in ep.glossary.characters:
            if key in c.voices:
                continue
            ranked = sorted(catalog, key=lambda v: (_score(c, v), -used.get(v["id"], 0)), reverse=True)
            best = ranked[0]
            c.voices[key] = best["id"]
            reuse = used.get(best["id"], 0)
            if reuse:  # two characters on one voice: shift pitch so listeners can tell them apart
                c.pitch_offset_hz = (-6 if reuse % 2 else 6) * ((reuse + 1) // 2)
            used[best["id"]] = reuse + 1


def voice_for(ep: Episode, speaker: str, provider: str, lang: str) -> tuple[str, int]:
    c = ep.glossary.character(speaker)
    if c is None or f"{provider}:{lang}" not in c.voices:
        catalog = load_pack(lang).voice_catalog(provider)
        return catalog[0]["id"], 0
    return c.voices[f"{provider}:{lang}"], c.pitch_offset_hz
