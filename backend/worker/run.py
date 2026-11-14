"""RQ worker: picks dubbing jobs from Redis. Run: python worker/run.py"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis  # noqa: E402
from rq import Queue, Worker  # noqa: E402

from app.db import init_db  # noqa: E402
from app.queue import QUEUE_NAME  # noqa: E402

if __name__ == "__main__":
    url = os.environ.get("REDIS_URL")
    if not url:
        sys.exit("REDIS_URL is not set - without Redis the API runs jobs in-process, no worker needed.")
    init_db()
    conn = redis.from_url(url)
    Worker([Queue(QUEUE_NAME, connection=conn)], connection=conn).work()
