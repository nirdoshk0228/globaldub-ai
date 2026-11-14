"""Job queue: Redis + RQ in production, a small thread pool when REDIS_URL is not set (local dev, tests)."""
from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor

from .settings import app_settings

log = logging.getLogger("globaldub.queue")
_pool: ThreadPoolExecutor | None = None
QUEUE_NAME = "globaldub"


def _inline(fn, *args) -> Future:
    global _pool
    if _pool is None:
        _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gd-worker")

    def wrapped():
        try:
            fn(*args)
        except Exception:
            log.exception("inline task failed")

    return _pool.submit(wrapped)


def enqueue(task: str, *args, timeout: int = 1800):
    """task is 'run_job' or 'rerender'."""
    from . import tasks

    url = app_settings().redis_url
    if not url:
        return _inline(getattr(tasks, task), *args)
    import redis
    from rq import Queue, Retry

    q = Queue(QUEUE_NAME, connection=redis.from_url(url))
    return q.enqueue(f"app.tasks.{task}", *args, job_timeout=timeout,
                     retry=Retry(max=1, interval=30) if task == "run_job" else None)


def queue_depth() -> int | None:
    url = app_settings().redis_url
    if not url:
        return None
    import redis
    from rq import Queue

    return len(Queue(QUEUE_NAME, connection=redis.from_url(url)))
