import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT.parent / "samples" / "virasat_ep01.json"

# environment must be set before any app/pipeline module reads it
_TMP = Path(tempfile.mkdtemp(prefix="gd-test-"))
os.environ.update({
    "GD_LLM": "mock", "GD_TTS": "mock", "GD_TTS_FALLBACK": "",
    "GD_WORK_DIR": str(_TMP / "work"), "GD_STORAGE_DIR": str(_TMP / "storage"),
    "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}", "REDIS_URL": "", "S3_BUCKET": "",
    "ADMIN_TOKEN": "test-admin", "JOBS_PER_IP_PER_DAY": "2", "MAX_LANGS_PER_JOB": "3",
})


@pytest.fixture(scope="session")
def _isolated_env():
    return _TMP
