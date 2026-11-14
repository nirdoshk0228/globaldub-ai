"""Speech recognition + optional diarization, and loaders for already-written scripts.

Deployed: Groq Whisper API (fast, no GPU on the server).  Local: faster-whisper (+ pyannote).
Script input (JSON / SRT / "Speaker: line" text) skips ASR entirely.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

from .. import audio
from ..config import Settings
from ..langpacks import load_pack
from ..schemas import Segment


class ASRError(RuntimeError):
    pass


def _segments_from_raw(raw: list[dict]) -> list[Segment]:
    out = []
    for i, r in enumerate(raw, 1):
        text = (r.get("text") or "").strip()
        if not text:
            continue
        out.append(
            Segment(
                id=f"s{i:03d}",
                start=round(float(r["start"]), 3),
                end=round(float(r["end"]), 3),
                speaker=r.get("speaker") or "Speaker 1",
                text=text,
            )
        )
    return out


def transcribe_groq(path: Path, source_lang: str, s: Settings) -> list[Segment]:
    if not s.groq_api_key:
        raise ASRError("GROQ_API_KEY missing (free key at console.groq.com)")
    small = path.with_suffix(".asr.flac")
    audio.run_ffmpeg("-i", str(path), "-ac", "1", "-ar", "16000", str(small))  # keeps uploads well under the size limit
    with small.open("rb") as f:
        r = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {s.groq_api_key}"},
            data={
                "model": s.groq_asr_model,
                "language": load_pack(source_lang).asr_code or source_lang[:2],
                "response_format": "verbose_json",
                "timestamp_granularities[]": "segment",
                "temperature": "0",
            },
            files={"file": (small.name, f, "audio/flac")},
            timeout=300,
        )
    small.unlink(missing_ok=True)
    if r.status_code != 200:
        raise ASRError(f"Groq HTTP {r.status_code}: {r.text[:300]}")
    return _segments_from_raw(r.json().get("segments", []))


def transcribe_local(path: Path, source_lang: str, s: Settings) -> list[Segment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise ASRError("pip install faster-whisper to transcribe locally") from e
    model = WhisperModel(s.whisper_model, compute_type="int8")
    segs, _ = model.transcribe(str(path), language=load_pack(source_lang).asr_code or None, vad_filter=True)
    raw = [{"start": x.start, "end": x.end, "text": x.text} for x in segs]
    segments = _segments_from_raw(raw)
    if s.diarization:
        segments = diarize(path, segments, s)
    return segments


def diarize(path: Path, segments: list[Segment], s: Settings) -> list[Segment]:
    """Assign speakers with pyannote (local only; needs HF_TOKEN and the model licence accepted)."""
    from pyannote.audio import Pipeline

    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=s.hf_token)
    turns = [(t.start, t.end, spk) for t, _, spk in pipe(str(path)).itertracks(yield_label=True)]
    for seg in segments:
        best, overlap = None, 0.0
        for a, b, spk in turns:
            o = min(b, seg.end) - max(a, seg.start)
            if o > overlap:
                best, overlap = spk, o
        if best:
            seg.speaker = f"Speaker {int(best.split('_')[-1]) + 1}"
    return segments


def transcribe_mock(path: Path, source_lang: str, s: Settings) -> list[Segment]:
    """Offline stand-in: cut lines at silences (energy VAD) and fill placeholder text. For tests and demos."""
    import numpy as np

    x = audio.load(audio.to_wav(path, path.with_suffix(".mock.wav")))
    hop = int(0.02 * audio.SR)
    env = audio.moving_average(np.abs(x), hop * 5)[::hop]
    on = env > max(0.01, 0.25 * float(np.percentile(env, 90)))
    raw, start, quiet = [], None, 0
    for i, v in enumerate(on):
        if v:
            start = i if start is None else start
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet * 0.02 >= 0.3:
                end = i - quiet + 1
                if (end - start) * 0.02 >= 0.4:
                    raw.append({"start": start * 0.02, "end": end * 0.02})
                start, quiet = None, 0
    if start is not None:
        raw.append({"start": start * 0.02, "end": len(on) * 0.02})
    for k, r in enumerate(raw, 1):
        r["text"] = f"यह संवाद नंबर {k} है।"
    return _segments_from_raw(raw)


def transcribe(path: Path, source_lang: str, s: Settings) -> list[Segment]:
    if s.asr_provider == "mock":
        return transcribe_mock(path, source_lang, s)
    if s.asr_provider == "faster_whisper":
        return transcribe_local(path, source_lang, s)
    return transcribe_groq(path, source_lang, s)


# ---------------------------------------------------------------- script loaders

def _estimate_timings(rows: list[dict], source_lang: str, gap: float = 0.45) -> None:
    pack = load_pack(source_lang)
    t = 0.5
    for r in rows:
        if "start" in r and "end" in r:
            t = float(r["end"]) + gap
            continue
        dur = max(1.0, pack.estimate_seconds(r["text"]))
        r["start"], r["end"] = round(t, 3), round(t + dur, 3)
        t += dur + gap + (0.4 if r.get("emotion") in ("shocked", "sad", "broken") else 0)


def load_script(path: Path, source_lang: str = "hi-IN") -> tuple[list[Segment], dict]:
    """Returns (segments, meta). meta may carry title / synopsis / characters / glossary from a JSON script."""
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    if path.suffix == ".json":
        data = json.loads(text)
        meta = {k: v for k, v in data.items() if k != "lines"}
        rows = data["lines"]
    elif path.suffix == ".srt":
        rows = []
        for block in re.split(r"\n\s*\n", text.strip()):
            parts = block.strip().splitlines()
            if len(parts) < 3:
                continue
            m = re.match(r"(\S+) --> (\S+)", parts[1])
            if not m:
                continue
            body = " ".join(parts[2:])
            spk = "Speaker 1"
            sm = re.match(r"^\s*\[?([^:\]]{1,30})[\]:]\s*(.+)$", body)
            if sm:
                spk, body = sm.group(1).strip(), sm.group(2).strip()
            rows.append({"start": _srt_t(m.group(1)), "end": _srt_t(m.group(2)), "speaker": spk, "text": body})
    else:  # plain text: "Speaker (emotion): line"
        rows = []
        for ln in text.splitlines():
            m = re.match(r"^\s*([^:(]{1,30}?)\s*(?:\(([^)]+)\))?\s*:\s*(.+)$", ln)
            if m:
                rows.append({"speaker": m.group(1).strip(), "emotion": (m.group(2) or "neutral").strip(), "text": m.group(3).strip()})
    _estimate_timings(rows, source_lang)
    segs = []
    for i, r in enumerate(rows, 1):
        segs.append(
            Segment(
                id=r.get("id") or f"s{i:03d}",
                start=float(r["start"]),
                end=float(r["end"]),
                speaker=r.get("speaker", "Speaker 1"),
                text=r["text"],
                emotion=r.get("emotion", "neutral"),
                reference=r.get("reference", {}),
            )
        )
    return segs, meta


def _srt_t(s: str) -> float:
    h, m, rest = s.replace(".", ",").split(":")
    sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
