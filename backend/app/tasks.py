"""Background tasks. Executed by the RQ worker in production, or by in-process threads in local dev."""
from __future__ import annotations

import logging
import traceback

from globaldub.config import get_settings
from globaldub.pipeline import Ctx, ingest, rerender_line, run_track
from globaldub.storage import get_storage

from . import repo
from .db import Job

log = logging.getLogger("globaldub.tasks")


def run_job(job_id: str) -> None:
    s = get_settings()
    ep = repo.load_episode(job_id)
    persist = repo.WorkerPersister(job_id, ep.target_langs, ingest=True)
    ctx = Ctx(settings=s, storage=get_storage(), on_change=persist)

    def mark(status: str, error: str | None = None):
        def fn(latest, job: Job):
            job.status, job.error = status, error
        repo.update(job_id, fn)

    try:
        mark("processing")
        ingest(ctx, ep)
    except Exception as e:
        log.exception("ingest failed for %s", job_id)
        for t in ep.tracks.values():
            t.status, t.error = "failed", f"ingest: {e}"
        persist(ep)
        mark("failed", f"{type(e).__name__}: {e}")
        return

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=max(1, s.parallel_tracks)) as pool:
        list(pool.map(lambda lang: run_track(ctx, ep, lang), ep.target_langs))
    persist(ep)
    log.info("job %s finished: %s", job_id, {k: v.status for k, v in ep.tracks.items()})


def rerender(job_id: str, lang: str, seg_id: str) -> None:
    ep = repo.load_episode(job_id)
    ctx = Ctx(settings=get_settings(), storage=get_storage())
    try:
        rerender_line(ctx, ep, lang, seg_id)
    except Exception:
        log.error("rerender failed %s/%s/%s\n%s", job_id, lang, seg_id, traceback.format_exc())
        line = ep.tracks[lang].line(seg_id)
        from globaldub.schemas import QAFlag

        line.flags.append(QAFlag(check="render_error", severity="high", source="audio",
                                 message="Could not re-voice this line. Try again."))
    repo.merge_line_render(job_id, ep, lang, seg_id)
