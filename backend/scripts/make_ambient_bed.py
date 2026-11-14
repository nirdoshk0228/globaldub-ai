"""Generate an original, royalty-free tension underscore for the demo episode (no samples, pure synthesis).

    python scripts/make_ambient_bed.py ../samples/virasat_bed.mp3 --seconds 180

A slow minor-key pad (Am - F - Dm - E), a low drone, a soft heartbeat pulse and a little room tone:
the kind of bed a family-drama audio series sits on. Because it is generated here, it can be published.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from globaldub import audio  # noqa: E402

SR = audio.SR
CHORDS = [  # (name, MIDI notes)
    ("Am", [45, 52, 57, 60, 64]),
    ("F", [41, 53, 57, 60, 65]),
    ("Dm", [38, 50, 57, 62, 65]),
    ("E", [40, 52, 56, 59, 64]),
]


def hz(m: float) -> float:
    return 440.0 * 2 ** ((m - 69) / 12)


def pad(freqs: list[float], seconds: float, rng: np.random.Generator) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    out = np.zeros_like(t)
    for f in freqs:
        for detune in (-0.12, 0.0, 0.11):          # three slightly detuned voices = a soft chorus
            ff = f * 2 ** (detune / 12)
            ph = rng.uniform(0, 2 * np.pi)
            out += np.sin(2 * np.pi * ff * t + ph) + 0.25 * np.sin(2 * np.pi * 2 * ff * t + ph)
    attack, release = 2.5, 2.5
    env = np.minimum(1, t / attack) * np.minimum(1, (seconds - t) / release)
    return out * np.clip(env, 0, 1) / (len(freqs) * 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--seconds", type=float, default=180)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)

    n = int(a.seconds * SR)
    mix = np.zeros(n + SR * 12)
    step = 8.0                                        # one chord every 8 s, overlapping by 2.5 s
    pos, k = 0.0, 0
    while pos < a.seconds:
        _, notes = CHORDS[k % len(CHORDS)]
        seg = pad([hz(m) for m in notes], step + 2.5, rng)
        i = int(pos * SR)
        mix[i : i + len(seg)] += seg[: len(mix) - i]
        pos += step
        k += 1
    mix = mix[:n]
    t = np.arange(n) / SR
    mix += 0.35 * np.sin(2 * np.pi * hz(33) * t) * (0.6 + 0.4 * np.sin(2 * np.pi * t / 23))   # low A drone

    beat = np.zeros(n)                                # heartbeat: lub-dub every 1.6 s, slowly fading in
    thump_t = np.arange(int(0.18 * SR)) / SR
    thump = np.sin(2 * np.pi * 52 * thump_t) * np.exp(-thump_t * 28)
    for start in np.arange(4.0, a.seconds - 1, 1.6):
        for off, amp in ((0.0, 1.0), (0.26, 0.6)):
            i = int((start + off) * SR)
            m = max(0, min(len(thump), n - i))
            beat[i : i + m] += amp * thump[:m]
    mix += 0.5 * beat * np.clip((t - 4) / 30, 0, 1)

    noise = rng.normal(0, 1, n)                       # room tone: very quiet, smoothed noise
    mix += 0.02 * audio.moving_average(noise.astype(np.float32), 40)

    mix /= np.max(np.abs(mix)) + 1e-9
    mix *= 10 ** (-17 / 20)                           # peak around -17 dBFS so dialogue sits on top
    mix = audio.fit_bed(mix.astype(np.float32), n, fade_in_s=1.0, fade_out_s=3.0)
    tmp = a.out.with_suffix(".tmp.wav")
    sf.write(str(tmp), mix, SR)
    if a.out.suffix.lower() == ".mp3":
        audio.to_mp3(tmp, a.out)
        tmp.unlink()
    else:
        tmp.replace(a.out)
    print(f"wrote {a.out} ({a.seconds:.0f}s)")


if __name__ == "__main__":
    main()
