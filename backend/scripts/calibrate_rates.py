"""Calibrate each language pack's speaking rate (chars_per_second) from real voiced audio.

The duration check and the localizer's character budget both depend on chars_per_second. Guessed rates
cause false "too long" flags (or miss real overruns), so measure them from a finished run:

    python scripts/calibrate_rates.py .work/<id>/episode.json            # show measured vs configured
    python scripts/calibrate_rates.py .work/<id>/episode.json --write    # update langpacks/*.yaml
"""
from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from globaldub.langpacks import PACK_DIR, load_pack  # noqa: E402
from globaldub.schemas import Episode  # noqa: E402


def letters(text: str, pack) -> int:
    # same counting rule the pipeline uses
    return round(pack.estimate_seconds(text) * pack.chars_per_second)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("episodes", type=Path, nargs="+")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    samples: dict[str, list[float]] = {}
    for path in a.episodes:
        ep = Episode.model_validate_json(path.read_text(encoding="utf-8"))
        src = load_pack(ep.source_lang)
        if ep.source_audio_key:  # source timings are real audio (voiced script or transcribed audio)
            for seg in ep.segments:
                if seg.duration >= 0.8:
                    samples.setdefault(ep.source_lang, []).append(letters(seg.text, src) / seg.duration)
        for lang, t in ep.tracks.items():
            pack = load_pack(lang)
            for ln in t.lines:
                if ln.tts_duration and ln.tts_duration >= 0.8 and ln.target_text:
                    raw = ln.tts_duration * ln.stretch      # undo the speed-up to get natural speech length
                    samples.setdefault(lang, []).append(letters(ln.target_text, pack) / raw)

    print(f"{'lang':8} {'lines':>5} {'configured':>11} {'measured':>9}")
    for lang, vals in sorted(samples.items()):
        pack = load_pack(lang)
        med = statistics.median(vals)
        print(f"{lang:8} {len(vals):5d} {pack.chars_per_second:11.1f} {med:9.1f}")
        if a.write and len(vals) >= 10:
            f = PACK_DIR / f"{lang}.yaml"
            text = f.read_text(encoding="utf-8")
            text = re.sub(r"^chars_per_second: .*$", f"chars_per_second: {med:.1f}   # measured from {len(vals)} voiced lines",
                          text, count=1, flags=re.M)
            f.write_text(text, encoding="utf-8")
    if a.write:
        print("\nUpdated language packs (languages with at least 10 voiced lines).")


if __name__ == "__main__":
    main()
