"""API/worker settings (separate from pipeline settings so the pipeline stays importable on its own)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from globaldub.config import BACKEND_ROOT


def _e(k: str, d: str = "") -> str:
    """Empty or comment-only values ("KEY=" or "KEY=  # note") mean "use the default"."""
    v = os.getenv(k, "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":   # KEY="value" pasted with its quotes
        v = v[1:-1].strip()
    return d if not v or v.startswith("#") else v


@dataclass(frozen=True)
class AppSettings:
    database_url: str = field(default_factory=lambda: _e("DATABASE_URL", f"sqlite:///{BACKEND_ROOT / '.data' / 'globaldub.db'}"))
    redis_url: str = field(default_factory=lambda: _e("REDIS_URL"))          # empty -> in-process worker threads
    cors_origins: tuple = field(default_factory=lambda: tuple(o for o in _e("CORS_ORIGINS", "http://localhost:3000").split(",") if o))
    admin_token: str = field(default_factory=lambda: _e("ADMIN_TOKEN"))
    ip_salt: str = field(default_factory=lambda: _e("IP_SALT", "change-me"))

    # public-demo guardrails (admin token bypasses them)
    max_upload_mb: int = field(default_factory=lambda: int(_e("MAX_UPLOAD_MB", "20")))
    max_audio_seconds: int = field(default_factory=lambda: int(_e("MAX_AUDIO_SECONDS", "180")))
    max_script_lines: int = field(default_factory=lambda: int(_e("MAX_SCRIPT_LINES", "80")))
    max_langs_per_job: int = field(default_factory=lambda: int(_e("MAX_LANGS_PER_JOB", "3")))
    jobs_per_ip_per_day: int = field(default_factory=lambda: int(_e("JOBS_PER_IP_PER_DAY", "3")))
    edits_per_job: int = field(default_factory=lambda: int(_e("EDITS_PER_JOB", "60")))
    daily_budget_usd: float = field(default_factory=lambda: float(_e("DAILY_BUDGET_USD", "1.0")))


_s: AppSettings | None = None


def app_settings() -> AppSettings:
    global _s
    if _s is None:
        _s = AppSettings()
    return _s


def reset_app_settings() -> None:
    global _s
    _s = None
