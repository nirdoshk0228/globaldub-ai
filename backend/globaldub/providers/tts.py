"""Text-to-speech providers. Each returns a mono 24 kHz WAV at `out_wav`, trimmed of edge silence."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import time
import wave
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf

from .. import audio
from ..config import Settings
from ..schemas import Delivery

# emotion -> (rate %, pitch Hz, volume %) at full intensity; scaled by Delivery.intensity
EMOTION_PROSODY = {
    "neutral": (0, 0, 0),
    "angry": (8, 3, 15),
    "furious": (12, 4, 20),
    "sad": (-12, -4, -10),
    "broken": (-15, -5, -15),
    "happy": (6, 6, 5),
    "tender": (-8, -1, -10),
    "fearful": (10, 6, 0),
    "nervous": (8, 3, -5),
    "shocked": (4, 8, 10),
    "sarcastic": (-4, 2, 0),
    "suspicious": (-6, -2, -5),
    "firm": (-3, -2, 10),
    "defensive": (8, 2, 10),
    "worried": (-4, 1, -5),
    "mysterious": (-10, -3, -12),
    "whisper": (-8, -2, -30),
}
PACE_RATE = {"slow": -10, "normal": 0, "fast": 10}

EMOTION_STYLE_WORDS = {
    "angry": "angrily", "furious": "furiously", "sad": "sadly", "broken": "in a broken, tearful voice",
    "happy": "happily", "tender": "tenderly", "fearful": "fearfully", "nervous": "nervously",
    "shocked": "in shock", "sarcastic": "sarcastically", "suspicious": "suspiciously", "firm": "firmly",
    "defensive": "defensively", "worried": "in a worried tone", "mysterious": "in a low, mysterious tone",
    "whisper": "in a whisper",
}


def prosody(delivery: Delivery, pitch_offset_hz: int = 0) -> tuple[str, str, str]:
    r, p, v = EMOTION_PROSODY.get(delivery.emotion, (0, 0, 0))
    k = 0.5 + delivery.intensity  # 0.5 .. 1.5
    rate = int(r * k) + PACE_RATE.get(delivery.pace, 0)
    pitch = int(p * k) + pitch_offset_hz
    vol = int(v * k)
    return f"{rate:+d}%", f"{pitch:+d}Hz", f"{vol:+d}%"


class TTSError(RuntimeError):
    pass


class BaseTTS:
    name = "base"

    def __init__(self, settings: Settings):
        self.s = settings
        self.chars = 0

    def synth(self, text: str, voice: str, lang: str, delivery: Delivery, out_wav: Path, pitch_offset_hz: int = 0) -> float:
        raw = out_wav.with_suffix(".raw")
        self._synth(text, voice, lang, delivery, raw, pitch_offset_hz)
        audio.to_wav(raw, out_wav)
        raw.unlink(missing_ok=True)
        audio.trim_silence(out_wav)
        self.chars += len(text)
        return audio.duration(out_wav)

    def _synth(self, text, voice, lang, delivery, out_path, pitch_offset_hz) -> None:
        raise NotImplementedError

    def cost(self) -> float:
        return self.chars / 1000 * self.s.price_tts_per_1k_chars


class EdgeTTS(BaseTTS):
    """Microsoft Edge neural voices via the free `edge-tts` package. No key needed."""

    name = "edge"

    def _synth(self, text, voice, lang, delivery, out_path, pitch_offset_hz):
        import edge_tts

        rate, pitch, volume = prosody(delivery, pitch_offset_hz)

        async def go():
            await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume).save(str(out_path))

        last: Exception | None = None
        for attempt in range(3):
            try:
                asyncio.run(go())
                if out_path.exists() and out_path.stat().st_size > 0:
                    return
                last = TTSError("empty audio")
            except Exception as e:  # network hiccups are common, retry
                last = e
            time.sleep(1.5 * (attempt + 1))
        raise TTSError(f"edge-tts failed for voice {voice}: {last}")


class GeminiTTS(BaseTTS):
    """Gemini TTS: style is steered with a natural-language instruction."""

    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def _synth(self, text, voice, lang, delivery, out_path, pitch_offset_hz):
        if not self.s.gemini_api_key:
            raise TTSError("GEMINI_API_KEY missing")
        style = EMOTION_STYLE_WORDS.get(delivery.emotion, "")
        pace = {"slow": " slowly", "fast": " quickly"}.get(delivery.pace, "")
        prompt = f"Say{(' ' + style) if style else ''}{pace}: {text}"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
            },
        }
        r = httpx.post(
            self.URL.format(model=self.s.gemini_tts_model),
            headers={"x-goog-api-key": self.s.gemini_api_key},
            json=body,
            timeout=120,
        )
        if r.status_code != 200:
            raise TTSError(f"Gemini TTS HTTP {r.status_code}: {r.text[:300]}")
        part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm = base64.b64decode(part["data"])
        with wave.open(str(out_path), "wb") as w:  # Gemini returns raw 16-bit PCM, 24 kHz mono
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(pcm)


class ElevenLabsTTS(BaseTTS):
    name = "elevenlabs"

    def _synth(self, text, voice, lang, delivery, out_path, pitch_offset_hz):
        if not self.s.elevenlabs_api_key:
            raise TTSError("ELEVENLABS_API_KEY missing")
        intense = delivery.emotion not in ("neutral", "tender")
        body = {
            "text": text,
            "model_id": self.s.elevenlabs_model,
            "voice_settings": {
                "stability": round(0.6 - 0.35 * delivery.intensity if intense else 0.6, 2),
                "similarity_boost": 0.8,
                "style": round(0.15 + 0.5 * delivery.intensity if intense else 0.1, 2),
            },
        }
        r = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice}",
            headers={"xi-api-key": self.s.elevenlabs_api_key, "accept": "audio/mpeg"},
            json=body,
            timeout=120,
        )
        if r.status_code != 200:
            raise TTSError(f"ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
        out_path.write_bytes(r.content)


class MockTTS(BaseTTS):
    """Offline placeholder speech: syllable-shaped tones whose length follows the language's speaking rate."""

    name = "mock"

    def _synth(self, text, voice, lang, delivery, out_path, pitch_offset_hz):
        from ..langpacks import load_pack

        try:
            seconds = load_pack(lang).estimate_seconds(text)
        except KeyError:
            seconds = len(text) / 14
        rate, _, _ = prosody(delivery)
        seconds = max(0.4, seconds / (1 + int(rate.rstrip("%")) / 100))
        sr = audio.SR
        t = np.arange(int(seconds * sr)) / sr
        seed = int(hashlib.md5(voice.encode()).hexdigest()[:6], 16)
        base = 110 + seed % 120 + (60 if "female" in voice else 0) + pitch_offset_hz
        syll = 4.5
        env = np.clip(np.sin(np.pi * (t * syll % 1.0)), 0, 1) ** 2
        wav = 0.25 * env * (np.sin(2 * np.pi * base * t) + 0.4 * np.sin(2 * np.pi * 2 * base * t))
        sf.write(str(out_path), wav.astype(np.float32), sr, format="WAV")


PROVIDERS = {"edge": EdgeTTS, "gemini": GeminiTTS, "elevenlabs": ElevenLabsTTS, "mock": MockTTS}


def get_tts(name: str, settings: Settings) -> BaseTTS:
    return PROVIDERS[name](settings)
