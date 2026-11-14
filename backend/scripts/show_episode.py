"""Print a finished episode side by side: Hindi line, dub, back-translation, adaptation note, QA flags.

    python scripts/show_episode.py .work/<id>/episode.json            # all languages
    python scripts/show_episode.py .work/<id>/episode.json --flagged  # only lines QA flagged
Also writes <id>_review.txt next to the episode file so you can share it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from globaldub.schemas import Episode  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episode", type=Path)
    ap.add_argument("--flagged", action="store_true")
    ap.add_argument("--langs", default="")
    a = ap.parse_args()

    ep = Episode.model_validate_json(a.episode.read_text(encoding="utf-8"))
    langs = [x for x in a.langs.split(",") if x] or list(ep.tracks)
    out: list[str] = [f"{ep.title}  ({ep.id})", ""]
    for lang in langs:
        t = ep.tracks[lang]
        out += ["=" * 72, f"{lang}   status={t.status}   cost=${t.cost_usd:.4f}", "=" * 72]
        for seg in ep.segments:
            ln = t.line(seg.id)
            if a.flagged and ln.status != "flagged":
                continue
            out.append(f"\n{seg.id}  {seg.speaker} ({seg.emotion})  [{ln.status}, attempts={ln.attempts}, stretch={ln.stretch}]")
            out.append(f"  HI : {seg.text}")
            out.append(f"  {lang[:2].upper()} : {ln.target_text}")
            if ln.back_translation and not lang.startswith("en"):
                out.append(f"  means: {ln.back_translation}")
            if ln.adaptation_note:
                out.append(f"  note : {ln.adaptation_note}")
            for f in ln.flags:
                out.append(f"  FLAG {f.severity}/{f.source} {f.check}: {f.message} {f.suggestion}".rstrip())
        out.append("")
    text = "\n".join(out)
    print(text)
    dest = a.episode.with_name(f"{ep.id}_review.txt")
    dest.write_text(text, encoding="utf-8")
    print(f"\nSaved to {dest}")


if __name__ == "__main__":
    main()
