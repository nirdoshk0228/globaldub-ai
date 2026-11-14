"""Language packs: one YAML file per language. Adding a market = adding a file, no code changes."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Optional

import yaml
from pydantic import BaseModel, Field

from .config import BACKEND_ROOT

PACK_DIR = BACKEND_ROOT / "langpacks"

# Gemini and ElevenLabs voices are multilingual, so one list serves every language.
GEMINI_VOICES = [
    {"id": "Leda", "gender": "female", "age": "young"},
    {"id": "Kore", "gender": "female", "age": "adult"},
    {"id": "Aoede", "gender": "female", "age": "adult"},
    {"id": "Gacrux", "gender": "female", "age": "senior"},
    {"id": "Puck", "gender": "male", "age": "young"},
    {"id": "Orus", "gender": "male", "age": "adult"},
    {"id": "Charon", "gender": "male", "age": "adult"},
    {"id": "Algenib", "gender": "male", "age": "senior"},
]
ELEVENLABS_VOICES = [
    {"id": "21m00Tcm4TlvDq8ikWAM", "gender": "female", "age": "young"},
    {"id": "EXAVITQu4vr4xnSDxMaL", "gender": "female", "age": "young"},
    {"id": "MF3mGyEYCl7XYWbV9V6O", "gender": "female", "age": "adult"},
    {"id": "AZnzlk1XvdvUeBnXmlld", "gender": "female", "age": "adult"},
    {"id": "TxGEqnHWrfWFTfGW9XjX", "gender": "male", "age": "young"},
    {"id": "pNInz6obpgDQGcFmaJgB", "gender": "male", "age": "adult"},
    {"id": "ErXwobaYiN019PkySvjV", "gender": "male", "age": "adult"},
    {"id": "VR6AewLTigWG4xSOukaG", "gender": "male", "age": "senior"},
]
MOCK_VOICES = [
    {"id": f"mock-{g}-{a}", "gender": g, "age": a}
    for g in ("female", "male")
    for a in ("young", "adult", "senior")
]


class LangPack(BaseModel):
    code: str
    name: str
    native_name: str = ""
    market: str = "international"
    script: str = "Latin"
    script_ranges: list[str] = Field(default_factory=list)
    shares_source_script: bool = False
    direction: str = "ltr"
    chars_per_second: float = 14.0
    asr_code: str = ""
    register_guide: str = ""
    cultural_notes: str = ""
    numbers: dict[str, int] = Field(default_factory=dict)
    hindi_leak_words: list[str] = Field(default_factory=list)
    voices: dict[str, list[dict]] = Field(default_factory=dict)

    # ---------- script helpers ----------
    def _ranges(self) -> list[tuple[int, int]]:
        out = []
        for r in self.script_ranges:
            a, b = r.split("-")
            out.append((int(a, 16), int(b, 16)))
        return out

    def in_script(self, ch: str) -> bool:
        o = ord(ch)
        return any(a <= o <= b for a, b in self._ranges())

    def script_ratio(self, text: str) -> float:
        """Share of letters that belong to this language's script."""
        letters = [c for c in text if unicodedata.category(c)[0] in ("L", "M")]
        if not letters:
            return 1.0
        return sum(self.in_script(c) for c in letters) / len(letters)

    def estimate_seconds(self, text: str) -> float:
        letters = sum(1 for c in text if unicodedata.category(c)[0] in ("L", "M", "N"))
        return letters / self.chars_per_second

    def voice_catalog(self, provider: str) -> list[dict]:
        if provider == "gemini":
            return GEMINI_VOICES
        if provider == "elevenlabs":
            return ELEVENLABS_VOICES
        if provider == "mock":
            return MOCK_VOICES
        return self.voices.get(provider, [])

    # ---------- numbers ----------
    def extract_numbers(self, text: str) -> list[int]:
        """Return the numeric values mentioned in text, combining multipliers.

        "पचास लाख" -> [5000000], "5 million" -> [5000000], "two million" -> [2000000].
        """
        exact = {k: v for k, v in self.numbers.items() if not k.endswith("*")}
        prefixes = {k[:-1]: v for k, v in self.numbers.items() if k.endswith("*")}
        text = _normalize_digits(text)
        tokens = tokenize(text)

        def word_value(tok: str) -> Optional[int]:
            t = tok.lower()
            if t in exact:
                return exact[t]
            for p, v in prefixes.items():
                if t.startswith(p.lower()):
                    return v
            return None

        values: list[int] = []
        total, cur, active = 0.0, 0.0, False

        def flush():
            nonlocal total, cur, active
            if active:
                values.append(int(round(total + cur)))
            total, cur, active = 0.0, 0.0, False

        for tok in tokens:
            if tok[0].isdigit():
                if active and cur:
                    flush()
                try:
                    num = float(tok.replace(",", ""))
                except ValueError:
                    flush()
                    continue
                cur += num
                active = True
                continue
            v = word_value(tok)
            if v is None:
                flush()
                continue
            active = True
            if v == 100:
                cur = (cur or 1) * 100
            elif v >= 1000:
                total += (cur or 1) * v
                cur = 0
            else:
                cur += v
        flush()
        return [v for v in values if v > 1]   # "one/एक" is usually an article, not a quantity


_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[^\s\d!-/:-@\[-`{-~।॥،؛؟…“”‘’—–]+")


def tokenize(text: str) -> list[str]:
    """Unicode-safe word split: keeps Indic vowel signs (category M) inside words, unlike \\w."""
    return [t for t in _TOKEN.findall(text) if t]


def _normalize_digits(text: str) -> str:
    out = []
    for ch in text:
        if ch.isdigit() and not ch.isascii():
            out.append(str(unicodedata.digit(ch)))
        else:
            out.append(ch)
    return "".join(out)


@lru_cache(maxsize=None)
def load_pack(code: str) -> LangPack:
    path = PACK_DIR / f"{code}.yaml"
    if not path.exists():
        raise KeyError(f"No language pack for {code}. Add {path.name} to {PACK_DIR}.")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["numbers"] = {str(k): int(v) for k, v in (data.get("numbers") or {}).items()}
    return LangPack(**data)


def available_targets() -> list[LangPack]:
    packs = [load_pack(p.stem) for p in sorted(PACK_DIR.glob("*.yaml"))]
    return [p for p in packs if p.code != "hi-IN"]
