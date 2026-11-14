"""Character Bible agent: makes sure every speaker is a known character with a name in every target script."""
from __future__ import annotations

import json

from ..langpacks import load_pack
from ..providers.llm import BaseLLM
from ..schemas import Character, Episode

SYSTEM = """You are the character-bible editor for an Indian audio-drama studio that dubs shows into many languages.
For each character, return: gender (female/male/neutral), age (child/young/adult/senior), a one-line persona,
and the character's name written in each requested target language's script, as a native speaker would write it.
Infer from the dialogue. Respond with JSON only:
{"characters":[{"name":"...","gender":"...","age":"...","persona":"...","target_names":{"<lang>":"..."}}]}"""


def build_character_bible(ep: Episode, llm: BaseLLM) -> None:
    speakers = list(dict.fromkeys(s.speaker for s in ep.segments))
    for spk in speakers:
        if not ep.glossary.character(spk):
            ep.glossary.characters.append(Character(name=spk))

    langs = [lang for lang in ep.target_langs if load_pack(lang).script != "Latin"]
    todo = [
        c for c in ep.glossary.characters
        if c.gender == "neutral" or not c.persona or any(lang not in c.target_names for lang in langs)
    ]
    if not todo:
        return

    sample = [f"{s.speaker}: {s.text}" for s in ep.segments[:60]]
    user = json.dumps(
        {
            "synopsis": ep.synopsis,
            "target_languages": {lang: load_pack(lang).name for lang in langs},
            "known": [c.model_dump(include={"name", "source_aliases", "gender", "age", "persona"}) for c in todo],
            "dialogue_sample": sample,
        },
        ensure_ascii=False,
    )

    def mock():
        return {"characters": [{"name": c.name, "target_names": {lang: c.name for lang in langs}} for c in todo]}

    out = llm.complete_json(SYSTEM, user, mock=mock)
    by_name = {c.name.lower(): c for c in ep.glossary.characters}
    for row in out.get("characters", []):
        c = by_name.get(str(row.get("name", "")).lower())
        if not c:
            continue
        if c.gender == "neutral" and row.get("gender") in ("female", "male"):
            c.gender = row["gender"]
        if row.get("age") in ("child", "young", "adult", "senior") and c.age == "adult" and not c.persona:
            c.age = row["age"]
        c.persona = c.persona or row.get("persona", "")
        for lang, nm in (row.get("target_names") or {}).items():
            c.target_names.setdefault(lang, nm)
