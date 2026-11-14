"""Database layer (SQLAlchemy 2). SQLite locally, Postgres (Neon) in production via DATABASE_URL.

The Episode is stored as one JSON document per job: the pipeline reads/writes it as a unit, while
reviews and usage are relational rows for reporting and rate limiting.
"""
from __future__ import annotations

import datetime as dt
import threading
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .settings import app_settings

JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    source_lang: Mapped[str] = mapped_column(String(10), default="hi-IN")
    target_langs: Mapped[list] = mapped_column(JSONType, default=list)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    episode: Mapped[dict] = mapped_column(JSONType, default=dict)
    edit_token_hash: Mapped[str] = mapped_column(String(64))
    client_hash: Mapped[str] = mapped_column(String(64), index=True, default="")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    edits: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Review(Base):
    """Every human action in the Review Studio - this is where 'review effort' metrics come from."""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    lang: Mapped[str] = mapped_column(String(10))
    seg_id: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))          # approve | edit | reopen
    before_text: Mapped[str] = mapped_column(Text, default="")
    after_text: Mapped[str] = mapped_column(Text, default="")
    flag_checks: Mapped[list] = mapped_column(JSONType, default=list)   # what QA had flagged on this line
    seconds_spent: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine = None
_Session = None
_local_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def engine():
    global _engine, _Session
    if _engine is None:
        url = app_settings().database_url
        if url.startswith("postgres://"):              # Heroku/Render style URLs
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        kw: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        _engine = create_engine(url, **kw)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    Base.metadata.create_all(engine())


def reset_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None


@contextmanager
def session():
    engine()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


@contextmanager
def job_lock(job_id: str, timeout: int = 30):
    """Serialises read-modify-write on one job across API and worker processes (Redis lock when available)."""
    url = app_settings().redis_url
    if url:
        import redis

        lock = redis.from_url(url).lock(f"gd:lock:{job_id}", timeout=timeout, blocking_timeout=timeout)
        if not lock.acquire():
            raise TimeoutError(f"could not lock job {job_id}")
        try:
            yield
        finally:
            try:
                lock.release()
            except Exception:
                pass
    else:
        with _locks_guard:
            lk = _local_locks.setdefault(job_id, threading.Lock())
        with lk:
            yield


def jobs_today_for(client_hash: str) -> int:
    since = utcnow() - dt.timedelta(hours=24)
    with session() as s:
        return s.scalar(select(func.count()).select_from(Job).where(Job.client_hash == client_hash, Job.created_at >= since)) or 0


def spend_today() -> float:
    since = utcnow() - dt.timedelta(hours=24)
    with session() as s:
        return float(s.scalar(select(func.coalesce(func.sum(Job.cost_usd), 0.0)).where(Job.created_at >= since, Job.is_demo.is_(False))) or 0.0)
