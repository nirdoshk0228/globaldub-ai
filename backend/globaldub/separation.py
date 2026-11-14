"""Split an uploaded mix into dialogue and background (music & effects).

Studios deliver dubs on top of an "M&E" track: everything except the dialogue. When a show has no M&E
track, we separate it from the final mix:

* demucs  - Meta's Demucs source-separation model (best quality; needs ~2-4 GB RAM, so local or a bigger server)
* center  - centre-channel cancellation for stereo audio: voices are usually panned centre, so L-R removes them.
            No ML, runs anywhere, but it also removes centred bass/drums and does nothing for mono recordings.
"""
from __future__ import annotations

import importlib.util
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf

from . import audio

log = logging.getLogger("globaldub")


@dataclass
class Stems:
    bed: Path                      # music & effects
    dialogue: Optional[Path]       # voices only (None when the method can't isolate them)
    method: str


def demucs_available() -> bool:
    return importlib.util.find_spec("demucs") is not None


def _demucs(src: Path, out_dir: Path, model: str) -> Stems:
    cmd = [sys.executable, "-m", "demucs.separate", "--two-stems", "vocals", "-n", model, "-o", str(out_dir), str(src)]
    log.info("separating dialogue from background with Demucs (%s) - this can take a few minutes on CPU", model)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"demucs failed: {res.stderr.strip()[-400:]}")
    stem_dir = out_dir / model / src.stem
    vocals, rest = stem_dir / "vocals.wav", stem_dir / "no_vocals.wav"
    dialogue = audio.to_wav(vocals, out_dir / "dialogue.wav")
    bed = audio.to_wav(rest, out_dir / "bed.wav")
    shutil.rmtree(out_dir / model, ignore_errors=True)
    return Stems(bed=bed, dialogue=dialogue, method="demucs")


def _center(src: Path, out_dir: Path) -> Optional[Stems]:
    stereo = out_dir / "stereo.wav"
    audio.run_ffmpeg("-i", str(src), "-ac", "2", "-ar", str(audio.SR), str(stereo))
    x, _ = sf.read(str(stereo), dtype="float32", always_2d=True)
    stereo.unlink(missing_ok=True)
    left, right = x[:, 0], x[:, 1]
    side = (left - right) / 2
    mid = (left + right) / 2
    if audio.active_rms(side, 0.002) < 0.08 * max(audio.active_rms(mid, 0.002), 1e-6):
        log.info("audio is (near) mono - centre-channel separation cannot remove the voices; keeping dialogue only")
        return None
    bed = out_dir / "bed.wav"
    sf.write(str(bed), (side * 1.4).astype(np.float32), audio.SR)
    return Stems(bed=bed, dialogue=None, method="center")


def separate(src: Path, out_dir: Path, method: str = "auto", model: str = "htdemucs") -> Optional[Stems]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if method == "none":
        return None
    if method in ("demucs", "auto") and demucs_available():
        try:
            return _demucs(src, out_dir, model)
        except Exception as e:
            if method == "demucs":
                raise
            log.warning("Demucs failed (%s); falling back to centre-channel separation", e)
    elif method == "demucs":
        raise RuntimeError("GD_SEPARATION=demucs but demucs is not installed (pip install demucs)")
    return _center(src, out_dir)
