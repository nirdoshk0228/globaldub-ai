"""Runtime settings, all overridable via environment variables (.env locally, dashboard env vars in prod)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import dotenv_values, load_dotenv

    _env_file = BACKEND_ROOT / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
        # "KEY=    # note" is parsed by python-dotenv as the value "# note"; treat it as "not set"
        for _k, _v in dotenv_values(_env_file).items():
            if _v and _v.strip().startswith("#") and os.environ.get(_k, "").strip().startswith("#"):
                os.environ.pop(_k, None)
    else:
        load_dotenv()
except Exception:
    pass


def _env(key: str, default: str = "") -> str:
    """Empty or comment-only values ("KEY=" or "KEY=  # note") mean "use the default"."""
    v = os.getenv(key, "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":   # KEY="value" pasted with its quotes
        v = v[1:-1].strip()
    return default if not v or v.startswith("#") else v


def _f(key: str, default: float) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


@dataclass
class Settings:
    # ---- providers ("mock" = fully offline) ----
    llm_provider: str = field(default_factory=lambda: _env("GD_LLM", "mock"))           # gemini | openai | mock
    tts_provider: str = field(default_factory=lambda: _env("GD_TTS", "mock"))           # edge | gemini | elevenlabs | mock
    tts_fallback: str = field(default_factory=lambda: _env("GD_TTS_FALLBACK", ""))      # used if primary fails
    asr_provider: str = field(default_factory=lambda: _env("GD_ASR", "groq"))           # groq | faster_whisper
    diarization: bool = field(default_factory=lambda: _env("GD_DIARIZE", "0") == "1")
    # background separation for audio uploads: auto (demucs if installed, else center-channel) | demucs | center | none
    separation: str = field(default_factory=lambda: _env("GD_SEPARATION", "auto"))
    demucs_model: str = field(default_factory=lambda: _env("DEMUCS_MODEL", "htdemucs"))
    duck_db: float = field(default_factory=lambda: _f("GD_DUCK_DB", 6.0))   # music dip under dialogue
    llm_judge: bool = field(default_factory=lambda: _env("GD_LLM_JUDGE", "1") == "1")

    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-flash-latest"))
    gemini_fallback_model: str = field(default_factory=lambda: _env("GEMINI_FALLBACK_MODEL", "gemini-flash-lite-latest"))
    gemini_judge_model: str = field(default_factory=lambda: _env("GEMINI_JUDGE_MODEL", "gemini-flash-lite-latest"))
    llm_rpm: float = field(default_factory=lambda: _f("GD_LLM_RPM", 8))          # requests/minute across all threads
    gemini_tts_model: str = field(default_factory=lambda: _env("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview"))

    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "https://openrouter.ai/api/v1"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "qwen/qwen3-235b-a22b"))

    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_asr_model: str = field(default_factory=lambda: _env("GROQ_ASR_MODEL", "whisper-large-v3"))
    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_model: str = field(default_factory=lambda: _env("ELEVENLABS_MODEL", "eleven_multilingual_v2"))
    hf_token: str = field(default_factory=lambda: _env("HF_TOKEN"))
    whisper_model: str = field(default_factory=lambda: _env("WHISPER_MODEL", "large-v3"))

    # ---- pipeline knobs ----
    batch_size: int = int(_f("GD_BATCH_SIZE", 14))   # lines per LLM call: fewer, bigger calls suit free-tier limits
    context_lines: int = 3
    max_retries: int = 2
    max_stretch: float = _f("GD_MAX_STRETCH", 1.25)
    parallel_tracks: int = int(_f("GD_PARALLEL_TRACKS", 3))

    # ---- unit prices (USD) for the cost dashboard ----
    price_llm_in_per_1m: float = _f("PRICE_LLM_IN_1M", 0.30)
    price_llm_out_per_1m: float = _f("PRICE_LLM_OUT_1M", 2.50)
    price_tts_per_1k_chars: float = _f("PRICE_TTS_1K", 0.0)
    price_asr_per_min: float = _f("PRICE_ASR_MIN", 0.0)

    work_dir: Path = field(default_factory=lambda: Path(_env("GD_WORK_DIR", str(BACKEND_ROOT / ".work"))))

    def __post_init__(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)


def get_settings(**overrides) -> Settings:
    s = Settings()
    for k, v in overrides.items():
        if v is not None and hasattr(s, k):
            setattr(s, k, v)
    return s
