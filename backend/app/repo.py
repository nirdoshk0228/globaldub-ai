"""Read/merge/write of Episode documents.

The worker and the API can touch the same job at the same time (a reviewer edits the Tamil track while
the Arabic track is still rendering). Writing the whole document from a stale copy would silently erase
the other party's change (the classic lost-update problem), so every write is a locked
read-merge-write that only replaces the parts the writer actually owns.
"""
from __future__ import annotations

import hashlib
from typing import Callable, Iterable

from globaldub.schemas import Episode, Track

from .db import Job, job_lock, session


def load_episode(job_id: str) -> Episode:
    with session() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise KeyError(job_id)
        return Episode.model_validate(job.episode)


def _aggregate_status(ep: Episode, current: str) -> str:
    st = [t.status for t in ep.tracks.values()]
    if not st or current == "failed" and not any(x == "ready" for x in st):
        return current
    if any(x == "running" for x in st):
        return "processing"
    if any(x == "queued" for x in st):
        return current if current in ("queued", "processing") else "processing"
    if all(x == "ready" for x in st):
        return "ready"
    if all(x == "failed" for x in st):
        return "failed"
    return "partial"


def _cost(ep: Episode) -> float:
    return round(sum(t.cost_usd for t in ep.tracks.values()) + sum(m.cost_usd for m in ep.ingest_metrics), 6)


def _write(job: Job, ep: Episode) -> None:
    job.episode = ep.model_dump(mode="json")
    job.status = _aggregate_status(ep, job.status)
    job.cost_usd = _cost(ep)


def update(job_id: str, fn: Callable[[Episode, Job], None]) -> Episode:
    """Locked read-modify-write. `fn` mutates the freshest Episode (and may touch the Job row)."""
    with job_lock(job_id):
        with session() as s:
            job = s.get(Job, job_id, with_for_update=True)
            if job is None:
                raise KeyError(job_id)
            ep = Episode.model_validate(job.episode)
            fn(ep, job)
            _write(job, ep)
            return ep


def _h(obj) -> str:
    return hashlib.sha1(obj.model_dump_json().encode()).hexdigest()


class WorkerPersister:
    """`on_change` callback for the pipeline: writes ingest data plus only the tracks that changed."""

    def __init__(self, job_id: str, langs: Iterable[str], ingest: bool = True):
        self.job_id = job_id
        self.langs = set(langs)
        self.ingest = ingest
        self.last: dict[str, str] = {}

    def __call__(self, ep: Episode) -> None:
        changed = [lang for lang in self.langs if lang in ep.tracks and self.last.get(lang) != _h(ep.tracks[lang])]
        ingest_sig = hashlib.sha1((ep.model_dump_json(include={"segments", "glossary", "source_audio_key", "bed_key",
                                                                "dialogue_key", "background"})).encode()).hexdigest()
        ingest_changed = self.ingest and self.last.get("_ingest") != ingest_sig
        if not changed and not ingest_changed:
            return

        def apply(latest: Episode, job: Job) -> None:
            if ingest_changed:
                latest.segments = ep.segments
                latest.glossary = ep.glossary
                latest.source_audio_key = ep.source_audio_key
                latest.bed_key, latest.dialogue_key = ep.bed_key, ep.dialogue_key
                latest.background = ep.background
                latest.ingest_metrics = ep.ingest_metrics
            for lang in changed:
                latest.tracks[lang] = ep.tracks[lang]

        update(self.job_id, apply)
        for lang in changed:
            self.last[lang] = _h(ep.tracks[lang])
        if ingest_changed:
            self.last["_ingest"] = ingest_sig


def merge_line_render(job_id: str, ep: Episode, lang: str, seg_id: str) -> None:
    """Persist the result of re-rendering one line without clobbering newer edits to other lines."""
    mine: Track = ep.tracks[lang]
    line = mine.line(seg_id)

    def apply(latest: Episode, job: Job) -> None:
        t = latest.tracks[lang]
        cur = t.line(seg_id)
        if cur.version == line.version:          # nobody edited this line again meanwhile
            idx = t.lines.index(cur)
            t.lines[idx] = line
        t.dub_key, t.srt_key, t.metrics = mine.dub_key, mine.srt_key, mine.metrics
        t.stage = "done" if t.status == "ready" else t.stage

    update(job_id, apply)
