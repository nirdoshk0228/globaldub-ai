"""Audio helpers built on FFmpeg + numpy: conversion, tempo fitting, timeline assembly, subtitles."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 24000


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("FFmpeg not found. Install it (https://ffmpeg.org) and make sure it is on PATH.")
    return exe


def run_ffmpeg(*args: str) -> None:
    cmd = [_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {res.stderr.strip()[:500]}")


def to_wav(src: str | Path, dst: str | Path, sr: int = SR) -> Path:
    run_ffmpeg("-i", str(src), "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst))
    return Path(dst)


def duration(path: str | Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


def load(path: str | Path, sr: int = SR) -> np.ndarray:
    data, file_sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if file_sr != sr:  # resample through ffmpeg to keep deps light
        tmp = Path(path).with_suffix(f".{sr}.wav")
        to_wav(path, tmp, sr)
        data, _ = sf.read(str(tmp), dtype="float32")
        tmp.unlink(missing_ok=True)
    return data


def trim_silence(path: str | Path, threshold_db: float = -45.0) -> None:
    """Strip leading/trailing silence that TTS engines add, so timing math is honest."""
    x = load(path)
    if x.size == 0:
        return
    thr = 10 ** (threshold_db / 20)
    idx = np.where(np.abs(x) > thr)[0]
    if idx.size == 0:
        return
    pad = int(0.03 * SR)
    x = x[max(0, idx[0] - pad) : min(len(x), idx[-1] + pad)]
    sf.write(str(path), x, SR)


def tempo(src: str | Path, dst: str | Path, factor: float) -> Path:
    """Speed up/down without changing pitch (FFmpeg atempo supports 0.5-2.0 per filter)."""
    factor = float(np.clip(factor, 0.5, 2.0))
    run_ffmpeg("-i", str(src), "-filter:a", f"atempo={factor:.4f}", "-ar", str(SR), "-ac", "1", str(dst))
    return Path(dst)


def cut(src: str | Path, start: float, end: float, dst: str | Path) -> Path:
    run_ffmpeg("-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-ac", "1", "-ar", str(SR), str(dst))
    return Path(dst)


def moving_average(x: np.ndarray, win: int) -> np.ndarray:
    """O(n) moving average via cumulative sums (np.convolve is too slow for minutes of audio)."""
    if win <= 1 or len(x) == 0:
        return x
    c = np.cumsum(np.insert(x.astype(np.float64), 0, 0.0))
    half = win // 2
    idx = np.arange(len(x))
    lo = np.clip(idx - half, 0, len(x))
    hi = np.clip(idx + half + 1, 0, len(x))
    return ((c[hi] - c[lo]) / np.maximum(hi - lo, 1)).astype(np.float32)


def active_rms(x: np.ndarray, floor: float = 0.01) -> float:
    """Loudness of the parts that actually contain sound (ignores silence between lines)."""
    a = x[np.abs(x) > floor]
    return float(np.sqrt(np.mean(a**2))) if a.size else 0.0


def fit_bed(bed: np.ndarray, n: int, fade_in_s: float = 0.4, fade_out_s: float = 2.0) -> np.ndarray:
    """Loop a short bed or trim a long one to n samples; quick fade-in (the first line comes early), slow fade-out."""
    if bed.size == 0:
        return np.zeros(n, dtype=np.float32)
    if len(bed) < n:
        bed = np.tile(bed, int(np.ceil(n / len(bed))))
    bed = bed[:n].astype(np.float32).copy()
    fi, fo = min(int(fade_in_s * SR), n // 4), min(int(fade_out_s * SR), n // 4)
    if fi > 0:
        bed[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)
    if fo > 0:
        bed[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)
    return bed


def assemble(
    clips: list[tuple[float, str]],
    total_seconds: float,
    out_wav: str | Path,
    bed: str | Path | None = None,
    duck_db: float = 6.0,
    dialogue_rms: float | None = None,
    loop_bed: bool = False,
    raw_clips: list[tuple[float, str]] | None = None,
    bed_mute: list[tuple[float, float]] | None = None,
) -> Path:
    """Place each (start_time, clip) on a timeline, match the original dialogue loudness, and mix it over the
    music & effects bed, ducking the bed by `duck_db` while someone is speaking."""
    n = int((total_seconds + 1.0) * SR)
    voice = np.zeros(n, dtype=np.float32)
    for start, path in clips:
        x = load(path)
        i = int(start * SR)
        if i >= n:
            continue
        j = min(n, i + len(x))
        voice[i:j] += x[: j - i]
    if dialogue_rms:
        cur = active_rms(voice)
        if cur > 0:
            voice *= min(4.0, dialogue_rms / cur)
    for start, path in raw_clips or []:   # original audio (e.g. sung lines) placed as-is, never rescaled
        x = load(path)
        i = int(start * SR)
        j = min(n, i + len(x))
        if i < n:
            voice[i:j] += x[: j - i]
    mix = voice
    if bed:
        b = load(bed)
        b = fit_bed(b, n) if loop_bed or len(b) < n else b[:n]
        env = moving_average(np.abs(voice), int(0.25 * SR))          # where speech is, smoothed (attack/release)
        speaking = np.clip(env / 0.015, 0.0, 1.0)
        gain = (10 ** (-duck_db * speaking / 20)).astype(np.float32)
        for a, z in bed_mute or []:        # the original mix already carries its own music there
            i, j = int(a * SR), min(n, int(z * SR))
            gain[i:j] = 0.0
        mix = voice + b * gain
    peak = float(np.max(np.abs(mix))) or 1.0
    if peak > 0.98:
        mix = mix / peak * 0.98
    sf.write(str(out_wav), mix.astype(np.float32), SR)
    return Path(out_wav)


def to_mp3(src: str | Path, dst: str | Path) -> Path:
    run_ffmpeg("-i", str(src), "-codec:a", "libmp3lame", "-q:a", "3", str(dst))
    return Path(dst)


def _ts(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)):03d}"


def write_srt(rows: list[tuple[float, float, str]], path: str | Path) -> Path:
    lines = []
    for k, (a, b, text) in enumerate(rows, 1):
        lines += [str(k), f"{_ts(a)} --> {_ts(b)}", text, ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return Path(path)
