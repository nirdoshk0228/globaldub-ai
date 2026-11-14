from globaldub.agents.qa import rule_checks
from globaldub.config import get_settings
from globaldub.episodes import episode_from_script
from globaldub.langpacks import load_pack
from globaldub.schemas import Line

from conftest import SAMPLE


def _ep(lang="en-US"):
    ep = episode_from_script(SAMPLE, [lang])
    for c in ep.glossary.characters:
        c.target_names.setdefault(lang, c.name)
    return ep


def checks(ep, sid, text, lang="en-US", back=""):
    return {f.check for f in rule_checks(ep.seg(sid), Line(seg_id=sid, target_text=text, back_translation=back),
                                         ep, lang, get_settings()) if f.severity != "low"}


def test_lakh_equals_million():
    assert load_pack("hi-IN").extract_numbers("पचास लाख") == [5_000_000]
    assert load_pack("en-US").extract_numbers("5 million") == [5_000_000]
    assert load_pack("ta-IN").extract_numbers("ஐம்பது லட்சத்துக்கு") == [5_000_000]


def test_reference_lines_are_clean():
    ep = _ep()
    noisy = [s.id for s in ep.segments if checks(ep, s.id, s.reference["en-US"]) - {"duration_overrun"}]
    assert noisy == []


def test_number_change_is_caught():
    ep = _ep()
    assert "number_mismatch" in checks(ep, "s003", "Don't lie to me! That land is worth 50 million rupees. 5 million!")


def test_name_swap_is_caught():
    ep = _ep()
    assert "name_mismatch" in checks(ep, "s001", "Arjun! Where are the land papers from the safe?")


def test_literal_idiom_is_caught_but_real_idiom_passes():
    ep = _ep()
    assert "literal_idiom" in checks(ep, "s007", "This girl has mixed our honour into the soil!")
    assert "literal_idiom" not in checks(ep, "s007", "This girl dragged our name through the mud!")


def test_untranslated_and_script():
    ep = _ep("ta-IN")
    got = checks(ep, "s001", "मीरा! तिजोरी से ज़मीन के कागज़ कहाँ गए?", "ta-IN")
    assert {"untranslated", "script_mismatch"} <= got


def test_marathi_hindi_leak():
    ep = _ep("mr-IN")
    got = checks(ep, "s002", "माँजी, मुझे नहीं पता… मैंने तो तिजोरी को हाथ भी नहीं लगाया।", "mr-IN")
    assert "untranslated" in got


def test_dropped_sentence_is_caught_without_hurting_clean_lines():
    ep = _ep()
    assert "omission" in checks(ep, "s012", "The land papers are gone, Vikram.")
    assert "omission" not in checks(ep, "s012", "The land papers are gone, Vikram. And she claims she knows nothing.")
