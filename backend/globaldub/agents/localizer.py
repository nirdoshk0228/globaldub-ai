"""Cultural Localization Agent.

Adapts (not translates) each line for the target market while respecting: the character bible, the
glossary, the language pack's register rules, the original emotion, and a per-line time budget so the
dub fits the original scene. On retries it receives the QA agent's feedback for that exact line.
"""
from __future__ import annotations

import json
import logging

from ..config import Settings
from ..langpacks import LangPack, load_pack
from ..providers.llm import BaseLLM
from ..schemas import Delivery, Episode, Line, Track

log = logging.getLogger("globaldub")

SYSTEM = """You are a senior dubbing localizer adapting Indian {source_name} audio dramas for {target_name} audiences.
You write lines that voice actors will perform, so they must sound like natural speech in {target_name}.

Rules:
1. Adapt, do not translate word-for-word. Replace every {source_name} idiom with an idiom or phrase of the same
   intensity that a native {target_name} speaker would really say. Never translate idioms literally.
2. Keep meaning, intent, and every fact: names, numbers, amounts, times, relationships. Do not add new facts.
3. Write character names exactly as given in the character list for {target_code}. Never transliterate
   {source_name} honorifics or kinship words (e.g. "maaji", "bhaiya", "beta"): use the target language's own
   word from the glossary or register notes, and use the same word every time for the same relationship.
4. Respect the time budget: each line has max_chars. Stay at or under it; cut filler words first.
5. Match the speaker's emotion. If source_emotion is "unknown", infer it from the words, the scene and
   how_it_was_said (loudness and speed measured from the original audio). Choose delivery: emotion (one of neutral, angry, furious, sad, broken, happy,
   tender, fearful, nervous, shocked, sarcastic, suspicious, firm, defensive, worried, mysterious, whisper),
   intensity 0-1, pace slow/normal/fast.
6. back_translation: a faithful, literal English rendering of YOUR {target_name} line (not of the source), so a
   reviewer who does not speak {target_name} can verify it. Keep kinship words as written (e.g. "Anna", "Athai"),
   never add a name that is not in your line, and reflect the real grammar (a command must read as a command).
8. Keep the sentence type of the source: a taunt or accusation stays a statement or question, never an order.
7. adaptation_note: one short sentence when you changed an idiom, kinship term, unit or cultural reference; else "".

Register and style for {target_name}:
{register}
Cultural notes:
{cultural}

Respond with JSON only:
{{"lines":[{{"id":"...","target":"...","back_translation":"...","adaptation_note":"...","emotion":"...","intensity":0.6,"pace":"normal"}}]}}"""


def _system(pack: LangPack, src: LangPack) -> str:
    return SYSTEM.format(
        source_name=src.name,
        target_name=pack.name,
        target_code=pack.code,
        register=pack.register_guide.strip() or "Natural conversational register.",
        cultural=pack.cultural_notes.strip() or "-",
    )


def _characters(ep: Episode, lang: str) -> list[dict]:
    return [
        {
            "name": c.name,
            "source_spelling": c.source_aliases,
            "name_in_target": c.target_names.get(lang, c.name),
            "gender": c.gender,
            "age": c.age,
            "persona": c.persona,
        }
        for c in ep.glossary.characters
    ]


def _terms(ep: Episode, lang: str) -> list[dict]:
    out = []
    for t in ep.glossary.terms:
        if t.do_not_translate:
            out.append({"source": t.source, "keep_as_is": True})
        elif lang in t.target:
            out.append({"source": t.source, "use": t.target[lang], "note": t.note})
    return out


def max_chars(seconds: float, pack: LangPack, s: Settings) -> int:
    return max(8, int(seconds * pack.chars_per_second * min(s.max_stretch, 1.15)))


def localize(
    ep: Episode,
    track: Track,
    seg_ids: list[str],
    llm: BaseLLM,
    s: Settings,
    feedback: dict[str, list[str]] | None = None,
) -> list[str]:
    """Localize the given segment ids in place. Returns ids that were updated."""
    pack, src = load_pack(track.lang), load_pack(ep.source_lang)
    feedback = feedback or {}
    order = [seg.id for seg in ep.segments]
    system = _system(pack, src)
    updated: list[str] = []

    for b in range(0, len(seg_ids), s.batch_size):
        batch = seg_ids[b : b + s.batch_size]
        first = order.index(batch[0])
        context = []
        for sid in order[max(0, first - s.context_lines) : first]:
            seg, ln = ep.seg(sid), track.line(sid)
            context.append({"speaker": seg.speaker, "source": seg.text, "target": ln.target_text})

        items = []
        for sid in batch:
            seg, ln = ep.seg(sid), track.line(sid)
            item = {
                "id": sid,
                "speaker": seg.speaker,
                "source": seg.text,
                "source_emotion": seg.emotion,
                "seconds": round(ep.slot(sid), 2),
                "max_chars": max_chars(ep.slot(sid), pack, s),
            }
            if seg.audio_hint:
                item["how_it_was_said"] = seg.audio_hint
            if sid in feedback and ln.target_text:
                item["previous_attempt"] = ln.target_text
                item["fix_these_problems"] = feedback[sid]
            items.append(item)

        user = json.dumps(
            {
                "synopsis": ep.synopsis,
                "target_language": f"{pack.name} ({pack.code})",
                "characters": _characters(ep, track.lang),
                "glossary": _terms(ep, track.lang),
                "previous_lines_for_context": context,
                "lines_to_localize": items,
            },
            ensure_ascii=False,
        )

        def mock(batch=batch):
            rows = []
            for sid in batch:
                seg = ep.seg(sid)
                ref = seg.reference.get(track.lang)
                rows.append(
                    {
                        "id": sid,
                        "target": ref or f"[{track.lang}] {seg.text}",
                        "back_translation": seg.reference.get("en-US", seg.text),
                        "adaptation_note": "",
                        "emotion": seg.emotion,
                        "intensity": 0.6,
                        "pace": "normal",
                    }
                )
            return {"lines": rows}

        log.info("[%s] adapting lines %d-%d of %d", track.lang, b + 1, b + len(batch), len(seg_ids))
        out = llm.complete_json(system, user, mock=mock)
        for row in out.get("lines", []):
            sid = row.get("id")
            if sid not in batch:
                continue
            ln = track.line(sid)
            new_text = str(row.get("target", "")).strip()
            if new_text != ln.target_text:
                ln.version += 1
            ln.target_text = new_text
            ln.back_translation = str(row.get("back_translation", "")).strip()
            ln.adaptation_note = str(row.get("adaptation_note", "")).strip()
            ln.delivery = _delivery(row, ep.seg(sid).emotion)
            ln.attempts += 1
            updated.append(sid)
    return updated


def _delivery(row: dict, fallback: str) -> Delivery:
    try:
        intensity = float(row.get("intensity", 0.5))
    except (TypeError, ValueError):
        intensity = 0.5
    pace = row.get("pace") if row.get("pace") in ("slow", "normal", "fast") else "normal"
    return Delivery(emotion=str(row.get("emotion") or fallback), intensity=max(0.0, min(1.0, intensity)), pace=pace)


def ensure_lines(ep: Episode, track: Track) -> None:
    have = {ln.seg_id for ln in track.lines}
    for seg in ep.segments:
        if seg.id not in have:
            track.lines.append(Line(seg_id=seg.id, status="kept" if seg.is_song else "pending",
                                    adaptation_note="Sung line kept in the original voice" if seg.is_song else ""))
