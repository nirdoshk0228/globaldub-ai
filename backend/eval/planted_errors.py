"""Planted-error benchmark for the QA agent.

Take clean localized lines, plant one known error per copy, run QA, and measure:
  * recall per error type   (did QA raise a medium/high flag on the corrupted line?)
  * diagnosis accuracy      (was the flag the *right* check?)
  * false-positive rate     (clean lines that got a medium/high flag)

Clean lines come from the script's reference translations, or from a finished pipeline run
(`--from-episode runs/<id>/episode.json`) for languages without references.

    python eval/planted_errors.py --langs en-US                       # offline, rules only
    GD_LLM=gemini python eval/planted_errors.py --langs en-US --judge   # rules + LLM judge
    python eval/planted_errors.py --from-episode .work/<id>/episode.json --langs ta-IN,ar-SA --judge
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from globaldub.agents.qa import idioms, llm_judge, rule_checks  # noqa: E402
from globaldub.config import get_settings  # noqa: E402
from globaldub.episodes import episode_from_script  # noqa: E402
from globaldub.langpacks import load_pack  # noqa: E402
from globaldub.providers.llm import get_judge_llm  # noqa: E402
from globaldub.schemas import Episode, Line, Track  # noqa: E402

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT.parent.parent / "samples" / "virasat_ep01.json"

LITERAL = {  # word-for-word renderings a weak model produces
    "मिट्टी में मिला": "This girl has mixed our honour into the soil!",
    "हाथ धो": "Just now. Mom, what happened? Behind whom have you fallen after washing your hands?",
    "नज़रें चुरा": "Vikram... why are you stealing your eyes?",
    "आँखों पर पट्टी": "Meera, child... forgive me. A cloth was tied over my eyes.",
}
EXPECTED_CHECK = {
    "omission_full": {"omission"},
    "omission_partial": {"omission", "meaning_drift"},
    "name_swap": {"name_mismatch"},
    "number_change": {"number_mismatch", "meaning_drift"},
    "untranslated": {"untranslated", "script_mismatch"},
    "literal_idiom": {"literal_idiom", "cultural"},
    "duration_bloat": {"duration_overrun"},
    "meaning_flip": {"meaning_drift"},
}
FLIPS = [  # semantic errors only an LLM judge can catch
    (r"\bdon't know\b", "know exactly"), (r"\bhaven't\b", "have"), (r"\bno need\b", "every need"),
    (r"\bwasn't the only one\b", "was the only one"), (r"\bNot a week\b", "Take a week"),
    (r"\bclosed\b", "opened"), (r"\bbroke\b", "kept"),
]


def _names(ep: Episode, lang: str) -> dict[str, str]:
    return {c.name: c.target_names.get(lang, c.name) for c in ep.glossary.characters}


def plant(kind: str, ep: Episode, seg, ln: Line, lang: str, rng: random.Random) -> Line | None:
    pack = load_pack(lang)
    bad = deepcopy(ln)
    bad.version += 1
    t = bad.target_text
    if kind == "omission_full":
        bad.target_text = ""
    elif kind == "omission_partial":
        parts = re.split(r"(?<=[.!?।…])\s+", t)
        if len(parts) >= 2:
            bad.target_text = parts[0]
        else:
            words = t.split()
            if len(words) < 6:
                return None
            bad.target_text = " ".join(words[: max(1, len(words) // 3)])
        bad.back_translation = bad.target_text if lang.startswith("en") else bad.back_translation
    elif kind == "name_swap":
        names = _names(ep, lang)
        present = [c for c in ep.glossary.characters if any(a in seg.text for a in c.source_aliases)
                   and names[c.name].lower() in t.lower()]
        if not present:
            return None
        c = present[0]
        other = rng.choice([n for k, n in names.items() if k != c.name])
        bad.target_text = re.sub(re.escape(names[c.name]), other, t, flags=re.I)
    elif kind == "number_change":
        src_vals = load_pack(ep.source_lang).extract_numbers(seg.text)
        if not src_vals:
            return None
        m = re.search(r"\d+", t)
        if m:
            n = int(m.group())
            bad.target_text = t[: m.start()] + str(n * 10 if n < 10 else n + 7) + t[m.end():]
        else:
            words = sorted(pack.numbers, key=len, reverse=True)
            hit = next((w for w in words if not w.endswith("*") and re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t, re.I)), None)
            if not hit:
                return None
            repl = next(w for w in pack.numbers if not w.endswith("*") and pack.numbers[w] not in (pack.numbers[hit], 1)
                        and pack.numbers[w] < 100)
            bad.target_text = re.sub(rf"(?<!\w){re.escape(hit)}(?!\w)", repl, t, count=1, flags=re.I)
    elif kind == "untranslated":
        bad.target_text = seg.text
    elif kind == "literal_idiom":
        lit = next((v for k, v in LITERAL.items() if k in seg.text), None)
        if not lit or not any(re.search(i["pattern"], seg.text) for i in idioms()):
            return None
        if lang.startswith("en"):
            bad.target_text = lit
        bad.back_translation = lit
    elif kind == "duration_bloat":
        # keep adding text until the line genuinely cannot fit its slot (needs > 1.7x the time available)
        filler = " and I want you to know that I mean every single word of it" if lang.startswith("en") else " " + t
        while pack.estimate_seconds(bad.target_text) <= max(seg.duration, 0.5) * 1.7:
            bad.target_text += filler
    elif kind == "meaning_flip":
        if not lang.startswith("en"):
            return None
        for pat, rep in FLIPS:
            if re.search(pat, t):
                bad.target_text = re.sub(pat, rep, t, count=1)
                bad.back_translation = bad.target_text
                break
        else:
            return None
    if bad.target_text == ln.target_text and bad.back_translation == ln.back_translation:
        return None
    return bad


def flags_for(ep: Episode, lang: str, lines: dict[str, Line], use_judge: bool, settings, llm=None) -> dict[str, list]:
    out = {sid: rule_checks(ep.seg(sid.split("#")[0]), ln, ep, lang, settings) for sid, ln in lines.items()}
    if use_judge:
        track = Track(lang=lang, lines=[ln.model_copy(update={"seg_id": sid}) for sid, ln in lines.items()])
        shadow = ep.model_copy(deep=True)
        shadow.segments = [ep.seg(sid.split("#")[0]).model_copy(update={"id": sid}) for sid in lines]
        judged = llm_judge(shadow, track, list(lines), llm or get_judge_llm(settings), settings)
        for sid, fl in judged.items():
            out[sid] = out[sid] + fl
    return out


def run(ep: Episode, lang: str, base: dict[str, Line], use_judge: bool, seed: int = 7) -> dict:
    s = get_settings()
    rng = random.Random(seed)
    llm = get_judge_llm(s) if use_judge else None
    clean_flags = flags_for(ep, lang, base, use_judge, s, llm)
    serious = lambda fl: [f for f in fl if f.severity in ("medium", "high")]  # noqa: E731
    clean_ids = [sid for sid in base if not serious(clean_flags[sid])]

    planted: dict[str, Line] = {}
    meta: dict[str, tuple[str, str]] = {}
    for kind in EXPECTED_CHECK:
        for sid in clean_ids:
            bad = plant(kind, ep, ep.seg(sid), base[sid], lang, rng)
            if bad is not None:
                key = f"{sid}#{kind}"
                planted[key], meta[key] = bad, (sid, kind)
    bad_flags = flags_for(ep, lang, planted, use_judge, s, llm) if planted else {}

    per: dict[str, dict] = {k: {"planted": 0, "detected": 0, "diagnosed": 0} for k in EXPECTED_CHECK}
    misses = []
    for key, (sid, kind) in meta.items():
        new = [f for f in serious(bad_flags[key])]
        per[kind]["planted"] += 1
        if new:
            per[kind]["detected"] += 1
            if any(f.check in EXPECTED_CHECK[kind] for f in new):
                per[kind]["diagnosed"] += 1
        else:
            misses.append({"line": sid, "type": kind, "text": planted[key].target_text[:90]})
    for k, v in per.items():
        v["recall"] = round(v["detected"] / v["planted"], 3) if v["planted"] else None
    total_p = sum(v["planted"] for v in per.values())
    total_d = sum(v["detected"] for v in per.values())
    fp = sum(1 for sid in base if serious(clean_flags[sid]))
    rule_types = [k for k in per if k != "meaning_flip"]
    rp = sum(per[k]["planted"] for k in rule_types)
    rd = sum(per[k]["detected"] for k in rule_types)
    return {
        "lang": lang,
        "judge": use_judge,
        "llm": s.llm_provider,
        "judge_models": dict(llm.models_used) if llm else {},
        "timings": "voiced" if ep.source_audio_key else "estimated",
        "clean_lines": len(base),
        "false_positive_lines": fp,
        "false_positive_rate": round(fp / max(1, len(base)), 3),
        "planted_total": total_p,
        "detected_total": total_d,
        "recall_total": round(total_d / max(1, total_p), 3),
        "recall_excluding_semantic": round(rd / max(1, rp), 3),
        "by_type": per,
        "misses": misses[:25],
        "timestamp": int(time.time()),
    }


def base_lines(ep: Episode, lang: str, from_episode: Episode | None) -> dict[str, Line]:
    if from_episode and lang in from_episode.tracks:
        return {ln.seg_id: ln.model_copy(update={"flags": [], "tts_version": -1})
                for ln in from_episode.tracks[lang].lines if ln.target_text}
    out = {}
    for seg in ep.segments:
        ref = seg.reference.get(lang)
        if ref:
            out[seg.id] = Line(seg_id=seg.id, target_text=ref, back_translation=seg.reference.get("en-US", ""))
    return out


def to_markdown(results: list[dict]) -> str:
    kinds = list(EXPECTED_CHECK)
    head = "| Error type | " + " | ".join(r["lang"] for r in results) + " |\n|---|" + "---|" * len(results)
    rows = []
    for k in kinds:
        cells = []
        for r in results:
            v = r["by_type"][k]
            cells.append(f"{v['detected']}/{v['planted']}" if v["planted"] else "-")
        rows.append(f"| {k} | " + " | ".join(cells) + " |")
    rows.append("| **Recall (all)** | " + " | ".join(f"{r['recall_total']:.0%}" for r in results) + " |")
    rows.append("| **False-positive rate** | " + " | ".join(f"{r['false_positive_rate']:.0%}" for r in results) + " |")
    return head + "\n" + "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", type=Path, default=SAMPLE)
    ap.add_argument("--from-episode", type=Path, default=None)
    ap.add_argument("--langs", default="en-US")
    ap.add_argument("--judge", action="store_true", help="also run the LLM judge (needs GD_LLM=gemini|openai)")
    args = ap.parse_args()

    from_ep = Episode.model_validate_json(args.from_episode.read_text()) if args.from_episode else None
    langs = [x for x in args.langs.split(",") if x]
    ep = from_ep or episode_from_script(args.script, langs)
    if from_ep is None:
        for c in ep.glossary.characters:  # Latin names for reference-based eval
            for lang in langs:
                c.target_names.setdefault(lang, c.name)
    results = []
    for lang in langs:
        base = base_lines(ep, lang, from_ep)
        if not base:
            print(f"[skip] {lang}: no clean lines (add references or pass --from-episode)")
            continue
        results.append(run(ep, lang, base, args.judge))
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    tag = "judge" if args.judge else "rules"
    for r in results:
        if r["judge_models"]:
            print(f"judge model calls for {r['lang']}: {r['judge_models']}")
    payload = {"generated_at": int(time.time()), "mode": tag, "results": results}
    (out_dir / f"latest_{tag}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    md = to_markdown(results)
    (out_dir / f"latest_{tag}.md").write_text(md)
    print(md)
    for r in results:
        if r["misses"]:
            print(f"\nMissed in {r['lang']}:")
            for m in r["misses"][:10]:
                print("  ", m)


if __name__ == "__main__":
    main()
