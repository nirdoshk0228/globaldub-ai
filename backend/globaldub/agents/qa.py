"""Localization QA Agent.

Layer 1 - deterministic rules (cheap, explainable, language-aware):
    omission, untranslated / Hindi leak, wrong script, character names, numbers (lakh/crore aware),
    glossary terms, literal idioms, duration budget.
Layer 2 - LLM judge on lines that changed: meaning drift, tone, register (honorifics, gender agreement).
Layer 3 - audio check after synthesis (see pipeline): real duration vs slot.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from functools import lru_cache

import yaml

from ..config import BACKEND_ROOT, Settings
from ..langpacks import LangPack, load_pack, tokenize
from ..providers.llm import BaseLLM
from ..schemas import SEV_ORDER, Episode, Line, QAFlag, Segment, Track


@lru_cache(maxsize=1)
def idioms() -> list[dict]:
    return yaml.safe_load((BACKEND_ROOT / "data" / "idioms_hi.yaml").read_text(encoding="utf-8"))


_SENT_END = re.compile(r"[.!?।॥؟…]+")


def count_sentences(text: str) -> int:
    """Sentences = runs of words ended by . ! ? । … (ellipses inside a line count as a break, as in dialogue)."""
    parts = [p for p in _SENT_END.split(text) if re.search(r"[^\W\d_]", p)]
    return max(1, len(parts))


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(word.lower())}", text.lower()) is not None


# ------------------------------------------------------------------ rule checks

def rule_checks(seg: Segment, ln: Line, ep: Episode, lang: str, s: Settings) -> list[QAFlag]:
    pack: LangPack = load_pack(lang)
    src: LangPack = load_pack(ep.source_lang)
    tgt = ln.target_text.strip()
    flags: list[QAFlag] = []

    # 1. omission
    if not tgt:
        return [QAFlag(check="omission", severity="high", message="Line is empty - dialogue was dropped.")]
    src_est = max(0.3, src.estimate_seconds(seg.text))
    ratio = pack.estimate_seconds(tgt) / src_est
    if src_est >= 1.2 and ratio < 0.45:
        flags.append(QAFlag(
            check="omission", severity="high" if ratio < 0.25 else "medium",
            message=f"Dub is only {ratio:.0%} as long as the source - content may be missing.",
            suggestion="Check that every clause of the source line is present.",
        ))

    # 1b. a whole sentence dropped: source has several sentences, the dub fewer, and is clearly shorter
    src_sents, tgt_sents = count_sentences(seg.text), count_sentences(tgt)
    if src_sents >= 2 and tgt_sents < src_sents and ratio < 0.75 and not any(f.check == "omission" for f in flags):
        flags.append(QAFlag(
            check="omission", severity="medium",
            message=f"Source has {src_sents} sentences, the dub has {tgt_sents} and is {ratio:.0%} as long - a sentence may be missing.",
            suggestion="Make sure every sentence of the source is carried over.",
        ))

    # 2. untranslated text / wrong script
    if pack.shares_source_script:
        st, tt = set(tokenize(seg.text)), set(tokenize(tgt))
        overlap = len(st & tt) / max(1, len(st | tt))
        if overlap > 0.6 and len(st) > 3:
            flags.append(QAFlag(check="untranslated", severity="high",
                                message=f"{overlap:.0%} of words are identical to the {src.name} source."))
        leaks = [w for w in pack.hindi_leak_words if w in tt]
        if leaks:
            flags.append(QAFlag(check="untranslated", severity="medium",
                                message=f"{src.name} words leaking into {pack.name}: {', '.join(leaks)}",
                                suggestion=f"Rewrite with {pack.name} grammar and vocabulary."))
    else:
        leaked = [c for c in tgt if src.in_script(c)]
        if len(leaked) >= 3:
            flags.append(QAFlag(check="untranslated", severity="high",
                                message=f"Contains untranslated {src.script} text."))
    ratio_script = pack.script_ratio(tgt)
    if ratio_script < 0.6:
        flags.append(QAFlag(check="script_mismatch", severity="high",
                            message=f"Only {ratio_script:.0%} of the line is in {pack.script} script."))

    # 3. character names
    present = []
    for c in ep.glossary.characters:
        if any(a and a in seg.text for a in c.source_aliases):
            present.append(c)
    for c in present:
        expected = c.target_names.get(lang, c.name)
        if expected.lower() not in tgt.lower():
            others = [o.target_names.get(lang, o.name) for o in ep.glossary.characters
                      if o is not c and o.target_names.get(lang, o.name).lower() in tgt.lower()]
            msg = f'"{c.name}" is named in the source but "{expected}" is missing'
            if others:
                msg += f' - looks swapped with "{others[0]}"'
            flags.append(QAFlag(check="name_mismatch", severity="high", message=msg + ".",
                                suggestion=f'Use "{expected}".'))

    # 4. numbers (value-level and counted, so "50 lakh" == "5 million" but 5M != 50M)
    src_nums = Counter(src.extract_numbers(seg.text))
    tgt_nums = Counter(pack.extract_numbers(tgt))
    missing = list((src_nums - tgt_nums).elements())
    added = list((tgt_nums - src_nums).elements()) if src_nums else []
    if missing:
        msg = f"Source mentions {', '.join(f'{n:,}' for n in missing)} but the dub "
        msg += f"has {', '.join(f'{n:,}' for n in tgt_nums.elements()) or 'no number'}."
        flags.append(QAFlag(check="number_mismatch", severity="high", message=msg,
                            suggestion="Keep every amount, time and count - convert units if needed."))
    elif added:
        flags.append(QAFlag(check="number_mismatch", severity="medium",
                            message=f"Dub adds {', '.join(f'{n:,}' for n in added)}, which is not in the source."))

    # 5. glossary terms
    for t in ep.glossary.terms:
        if t.source not in seg.text:
            continue
        if t.do_not_translate and t.source not in tgt:
            flags.append(QAFlag(check="glossary", severity="medium", message=f'"{t.source}" must stay as-is.'))
        elif lang in t.target and t.target[lang].lower() not in tgt.lower():
            flags.append(QAFlag(check="glossary", severity="low",
                                message=f'Glossary prefers "{t.target[lang]}" for "{t.source}".'))

    # 6. literal idioms (checked on English text: the line itself or its back-translation)
    english = tgt if lang.startswith("en") else ln.back_translation
    if english:
        english = english.lower()
        for idi in idioms():
            if re.search(idi["pattern"], seg.text):
                text = english
                for ok in idi.get("allowed", []):  # genuine target-language idioms that reuse a marker word
                    text = text.replace(ok.lower(), " ")
                hits = [m for m in idi["literal_markers"] if _has_word(text, m)]
                if hits:
                    flags.append(QAFlag(check="literal_idiom", severity="high",
                                        message=f'Idiom translated literally ("{hits[0]}"). It means: {idi["meaning"]}.',
                                        suggestion="Use a natural equivalent of the same intensity."))

    # 7. duration budget (estimate until real audio exists; the audio check then takes over)
    est = pack.estimate_seconds(tgt)
    slot = ep.slot(seg.id) if seg.duration > 0 else 0
    if ln.tts_version != ln.version and slot > 0 and est > slot * s.max_stretch:
        over = est / slot
        flags.append(QAFlag(check="duration_overrun", severity="high" if over > 1.6 else "medium",
                            message=f"~{est:.1f}s of speech for a {slot:.1f}s slot ({over:.0%}).",
                            suggestion=f"Shorten to about {int(slot * pack.chars_per_second)} characters."))
    return flags


# ------------------------------------------------------------------ LLM judge

JUDGE_SYSTEM = """You are a strict dubbing QA reviewer fluent in {source_name} and {target_name}.
For each line compare the {source_name} source with the {target_name} dub itself. An English back-translation
written by the translator is attached; it may hide errors, so never treat it as proof that the dub is right.
Read the {target_name} text directly, check every clause of the source is present in it, and report a
back_translation issue when the back-translation says something the dub does not.
Report ONLY real problems a native-speaking director would send back:
- meaning_drift: meaning changed, a fact or clause missing, or something added
- tone_mismatch: emotion/intensity differs from the source and the stated emotion
- register: wrong honorific/formality, or verb/pronoun gender agreement wrong for the person addressed
- cultural: an idiom translated literally, or something that will confuse or offend {target_name} viewers
- grammar: a wrong verb form, tense or mood that changes meaning (e.g. an accusation turned into an order),
  or a source word merely transliterated instead of translated
- back_translation: the English back-translation does not match the dub (invented names, softened meaning) -
  reviewers rely on it, so report it
Do not report style preferences. Use severity high only if a viewer would clearly notice. Always give a concrete corrected wording in "suggestion".
Respond with JSON only: {{"issues":[{{"id":"...","check":"...","severity":"low|medium|high","message":"...","suggestion":"..."}}]}}
Return {{"issues":[]}} when everything is fine."""


def llm_judge(ep: Episode, track: Track, seg_ids: list[str], llm: BaseLLM, s: Settings) -> dict[str, list[QAFlag]]:
    pack, src = load_pack(track.lang), load_pack(ep.source_lang)
    out: dict[str, list[QAFlag]] = {}
    system = JUDGE_SYSTEM.format(source_name=src.name, target_name=pack.name)
    step = max(s.batch_size * 2, 30)
    for b in range(0, len(seg_ids), step):
        batch = seg_ids[b : b + step]
        items = []
        for sid in batch:
            seg, ln = ep.seg(sid), track.line(sid)
            items.append({"id": sid, "speaker": seg.speaker, "source": seg.text, "source_emotion": seg.emotion,
                          "dub": ln.target_text, "back_translation": ln.back_translation,
                          "dub_emotion": ln.delivery.emotion})
        user = json.dumps({"characters": [c.model_dump(include={"name", "gender", "age"}) for c in ep.glossary.characters],
                           "lines": items}, ensure_ascii=False)
        res = llm.complete_json(system, user, mock=lambda: {"issues": []})
        for iss in res.get("issues", []):
            sid = iss.get("id")
            if sid not in batch or iss.get("severity") not in SEV_ORDER:
                continue
            out.setdefault(sid, []).append(QAFlag(check=str(iss.get("check", "meaning_drift")), severity=iss["severity"],
                                                  message=str(iss.get("message", "")), suggestion=str(iss.get("suggestion", "")),
                                                  source="llm"))
    return out


def set_status(ln: Line) -> None:
    if ln.status in ("approved", "edited", "kept"):
        return
    sev = ln.max_severity
    ln.status = "flagged" if sev in ("medium", "high") else "auto_ok"


def run_qa(ep: Episode, track: Track, seg_ids: list[str], llm: BaseLLM | None, s: Settings) -> None:
    seg_ids = [sid for sid in seg_ids if track.line(sid).status != "kept"]
    judged = llm_judge(ep, track, seg_ids, llm, s) if (llm is not None and s.llm_judge and seg_ids) else {}
    for sid in seg_ids:
        seg, ln = ep.seg(sid), track.line(sid)
        audio_flags = [f for f in ln.flags if f.source == "audio" and ln.tts_version == ln.version]
        ln.flags = rule_checks(seg, ln, ep, track.lang, s) + judged.get(sid, []) + audio_flags
        set_status(ln)
