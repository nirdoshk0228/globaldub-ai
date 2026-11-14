"""Read who is speaking and how, from the audio itself (no ML, numpy only).

* pitch (F0) per line  -> speaker gender, and splitting one "Speaker 1" into two voices when pitches clearly differ
* held notes           -> singing detection: sung lines are kept in the original instead of being read out by TTS
* loudness and speed   -> hints for the localizer's delivery (shouted / soft, fast / slow)

Typical adult F0: men ~85-165 Hz, women ~165-255 Hz. Singing holds a pitch for long stretches; speech glides.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from . import audio
from .schemas import Character, Episode

log = logging.getLogger("globaldub")
SR = audio.SR
FRAME, HOP = int(0.04 * SR), int(0.01 * SR)
FMIN, FMAX = 70.0, 420.0


@dataclass
class LineStats:
    seg_id: str
    f0: Optional[float]        # median pitch of voiced frames (Hz)
    voiced: float              # share of frames that are voiced
    held: float                # share of voiced time spent on held notes (singing cue)
    rms: float
    cps: float                 # characters per second (speaking speed)


def f0_track(x: np.ndarray) -> np.ndarray:
    """Pitch per 10 ms frame via normalised autocorrelation; 0 where unvoiced."""
    if len(x) < FRAME:
        return np.zeros(0)
    n = 1 + (len(x) - FRAME) // HOP
    frames = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::HOP][:n].astype(np.float64)
    frames = frames - frames.mean(axis=1, keepdims=True)
    energy = np.sqrt((frames**2).mean(axis=1))
    size = 1 << int(np.ceil(np.log2(2 * FRAME)))
    spec = np.fft.rfft(frames * np.hanning(FRAME), size, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, size, axis=1)[:, :FRAME]
    ac = ac / np.maximum(ac[:, :1], 1e-12)
    lo, hi = int(SR / FMAX), int(SR / FMIN)
    lag = lo + np.argmax(ac[:, lo:hi], axis=1)
    peak = ac[np.arange(n), lag]
    gate = max(0.01, 0.15 * float(np.percentile(energy, 90)))
    voiced = (peak > 0.45) & (energy > gate)
    return np.where(voiced, SR / lag, 0.0)


def _held_ratio(f0: np.ndarray, min_run_s: float = 0.2, tol_semitones: float = 0.35) -> float:
    v = f0 > 0
    if v.sum() < 5:
        return 0.0
    cents = np.zeros_like(f0)
    cents[v] = 12 * np.log2(f0[v] / 100.0)
    held = np.zeros(len(f0), dtype=bool)
    run_start = 0
    for i in range(1, len(f0) + 1):
        steady = i < len(f0) and v[i] and v[i - 1] and abs(cents[i] - cents[run_start]) < tol_semitones
        if not steady:
            if (i - run_start) * HOP / SR >= min_run_s and v[run_start]:
                held[run_start:i] = True
            run_start = i
    return float(held[v].mean())


def analyze(ep: Episode, wav: np.ndarray) -> list[LineStats]:
    out = []
    for seg in ep.segments:
        x = wav[int(seg.start * SR): int(seg.end * SR)]
        f0 = f0_track(x)
        v = f0[f0 > 0]
        out.append(LineStats(
            seg_id=seg.id,
            f0=float(np.median(v)) if v.size >= 5 else None,
            voiced=float((f0 > 0).mean()) if f0.size else 0.0,
            held=_held_ratio(f0),
            rms=audio.active_rms(x),
            cps=len(seg.text.replace(" ", "")) / max(seg.duration, 0.3),
        ))
    return out


def gender_from_f0(f0: Optional[float]) -> Optional[str]:
    if f0 is None:
        return None
    if f0 < 150:
        return "male"
    if f0 > 180:
        return "female"
    return None  # ambiguous band: leave it to the character bible


def split_by_pitch(ep: Episode, stats: list[LineStats]) -> bool:
    """One generic speaker but two clearly different voice pitches -> two speakers (lower = Speaker 1)."""
    speakers = {s.speaker for s in ep.segments}
    if len(speakers) != 1 or not next(iter(speakers)).lower().startswith("speaker"):
        return False
    pts = np.array([np.log2(st.f0) for st in stats if st.f0])
    if len(pts) < 4:
        return False
    lo, hi = np.percentile(pts, 20), np.percentile(pts, 80)
    for _ in range(20):                                   # 2-means on log-pitch
        cut = (lo + hi) / 2
        a, b = pts[pts <= cut], pts[pts > cut]
        if not len(a) or not len(b):
            return False
        lo, hi = a.mean(), b.mean()
    if hi - lo < 0.5 or min(len(a), len(b)) < 2:          # need > ~1.4x pitch ratio and 2+ lines each
        return False
    cut = (lo + hi) / 2
    for seg, st in zip(ep.segments, stats):
        if st.f0:
            seg.speaker = "Speaker 1" if np.log2(st.f0) <= cut else "Speaker 2"
    log.info("split one voice into two speakers by pitch (%.0f Hz vs %.0f Hz)", 2**lo, 2**hi)
    return True


def is_singing(st: LineStats) -> bool:
    return st.voiced > 0.55 and st.held > 0.35


def apply(ep: Episode, wav: np.ndarray) -> dict:
    """Annotate an ASR episode from its audio: speakers, genders, singing, delivery hints."""
    stats = analyze(ep, wav)
    split = split_by_pitch(ep, stats)
    by_spk: dict[str, list[float]] = {}
    for seg, st in zip(ep.segments, stats):
        if st.f0 and not is_singing(st):          # sung pitch says little about gender (male singers go high)
            by_spk.setdefault(seg.speaker, []).append(st.f0)
    for spk, f0s in by_spk.items():
        g = gender_from_f0(float(np.median(f0s)))
        c = ep.glossary.character(spk)
        if c is None:
            c = Character(name=spk)
            ep.glossary.characters.append(c)
        if g and c.gender == "neutral":
            c.gender = g
            c.persona = c.persona or f"{g} voice (from audio pitch ~{np.median(f0s):.0f} Hz)"

    med_rms = float(np.median([s.rms for s in stats if s.rms > 0] or [1]))
    med_cps = float(np.median([s.cps for s in stats] or [1]))
    songs = 0
    for seg, st in zip(ep.segments, stats):
        if is_singing(st):
            seg.is_song = True
            songs += 1
        loud = "loud" if st.rms > 1.6 * med_rms else "soft" if st.rms < 0.6 * med_rms else "normal"
        pace = "fast" if st.cps > 1.25 * med_cps else "slow" if st.cps < 0.75 * med_cps else "normal"
        seg.audio_hint = f"{loud}, {pace}"
        if seg.emotion == "neutral":
            seg.emotion = "unknown"
    log.info("audio analysis: %d speaker(s)%s, %d sung line(s) kept in original",
             len({s.speaker for s in ep.segments}), " (split by pitch)" if split else "", songs)
    return {"split": split, "songs": songs}
