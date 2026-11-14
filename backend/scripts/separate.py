"""Split a finished episode/song into dialogue and background stems on your own machine (Demucs).

    pip install demucs            # once; downloads PyTorch, ~1-2 GB
    python scripts/separate.py episode.mp3
      -> episode_dialogue.wav  (voices)   episode_background.wav  (music & effects)

Upload the original as the episode and *_background.wav as the background track: the free server cannot run
Demucs (too little memory), so heavy separation happens here and the server only mixes.
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from globaldub.separation import separate  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("input", type=Path)
ap.add_argument("--method", default="demucs", choices=["demucs", "center"])
a = ap.parse_args()
with tempfile.TemporaryDirectory() as tmp:
    stems = separate(a.input, Path(tmp), a.method)
    if stems is None:
        sys.exit("Nothing to separate (the audio is mono and --method center needs stereo).")
    out_bg = a.input.with_name(a.input.stem + "_background.wav")
    shutil.copyfile(stems.bed, out_bg)
    print("background:", out_bg)
    if stems.dialogue:
        out_d = a.input.with_name(a.input.stem + "_dialogue.wav")
        shutil.copyfile(stems.dialogue, out_d)
        print("dialogue:  ", out_d)
