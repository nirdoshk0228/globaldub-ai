"""Run the full pipeline from the command line, no server needed.

    python scripts/run_local.py ../samples/virasat_ep01.json --langs en-US,ta-IN
    GD_LLM=gemini GD_TTS=edge python scripts/run_local.py ../samples/virasat_ep01.json --langs en-US,ta-IN,ar-SA
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from globaldub.config import get_settings  # noqa: E402
from globaldub.episodes import AUDIO_SUFFIXES, episode_from_audio, episode_from_script, track_summary  # noqa: E402
from globaldub.pipeline import Ctx, run_episode  # noqa: E402
from globaldub.storage import get_storage  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "langgraph"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="script (.json/.srt/.txt) or audio file")
    ap.add_argument("--langs", default="en-US,ta-IN")
    ap.add_argument("--title", default=None)
    ap.add_argument("--bed", type=Path, default=None, help="optional background music & effects track")
    args = ap.parse_args()

    s = get_settings()
    storage = get_storage()
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]
    if args.input.suffix.lower() in AUDIO_SUFFIXES:
        key = storage.put_file(args.input, f"uploads/{args.input.name}")
        ep = episode_from_audio(key, langs, args.title or args.input.stem)
    else:
        ep = episode_from_script(args.input, langs, args.title)

    if args.bed:
        ep.bed_upload_key = storage.put_file(args.bed, f"uploads/{ep.id}_bed{args.bed.suffix}")

    out_dir = s.work_dir / ep.id
    out_dir.mkdir(parents=True, exist_ok=True)

    def save(e):
        (out_dir / "episode.json").write_text(e.model_dump_json(indent=2), encoding="utf-8")

    print(f"Episode {ep.id}: {len(ep.segments)} lines -> {langs} (llm={s.llm_provider}, tts={s.tts_provider})")
    run_episode(Ctx(settings=s, storage=storage, on_change=save), ep)
    for lang in langs:
        sm = track_summary(ep, lang)
        print(json.dumps({k: sm[k] for k in ("lang", "status", "error", "lines", "auto_ok", "flagged",
                                             "flags_by_check", "cost_per_minute_usd")}, ensure_ascii=False))
        t = ep.tracks[lang]
        if t.dub_key:
            path = getattr(storage, "path", None)
            print("   dub:", path(t.dub_key) if path else storage.url(t.dub_key))
    print("background:", ep.background)
    print("episode json:", out_dir / "episode.json")


if __name__ == "__main__":
    main()
