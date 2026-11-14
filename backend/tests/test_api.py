import time

import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLE


@pytest.fixture(scope="module")
def client(_isolated_env):
    from app.main import app
    with TestClient(app) as c:
        yield c


def wait_ready(client, job_id, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("ready", "partial", "failed"):
            return j
        time.sleep(0.5)
    raise AssertionError(f"job not finished: {j}")


def test_health_and_languages(client):
    assert client.get("/api/health").json()["ok"] is True
    codes = {x["code"] for x in client.get("/api/languages").json()}
    assert {"en-US", "ta-IN", "te-IN", "bn-IN", "mr-IN", "ar-SA", "pt-BR", "es-MX"} <= codes


def test_full_flow(client):
    with SAMPLE.open("rb") as f:
        r = client.post("/api/jobs", data={"target_langs": "en-US,ta-IN"}, files={"file": ("ep.json", f, "application/json")})
    assert r.status_code == 201, r.text
    job_id, token = r.json()["id"], r.json()["edit_token"]
    job = wait_ready(client, job_id)
    assert job["status"] == "ready", job
    en = next(t for t in job["tracks"] if t["lang"] == "en-US")
    assert en["dub_url"] and en["lines"] == 27

    track = client.get(f"/api/jobs/{job_id}/tracks/en-US").json()
    line = track["lines"][9]
    assert line["source_clip_url"] and line["tts_url"]

    # editing without the token is refused
    r = client.patch(f"/api/jobs/{job_id}/tracks/en-US/lines/{line['seg_id']}", json={"action": "approve"})
    assert r.status_code == 403
    # edit with token -> re-render of only that line
    r = client.patch(f"/api/jobs/{job_id}/tracks/en-US/lines/{line['seg_id']}",
                     json={"action": "edit", "target_text": "Vikram? When'd you get in?", "seconds_spent": 12},
                     headers={"x-edit-token": token})
    assert r.status_code == 200 and r.json()["rerender"] is True
    v = r.json()["line"]["version"]
    for _ in range(60):
        ln = client.get(f"/api/jobs/{job_id}/tracks/en-US").json()["lines"][9]
        if ln["tts_version"] == v:
            break
        time.sleep(0.3)
    assert ln["tts_version"] == v and ln["status"] == "edited"

    rep = client.get(f"/api/jobs/{job_id}/report").json()
    assert rep["review"]["en-US"]["edit"] == 1 and rep["review"]["en-US"]["seconds"] == 12


def test_validation_and_limits(client):
    r = client.post("/api/jobs", data={"target_langs": "xx-YY"}, files={"file": ("a.json", b"{}", "application/json")})
    assert r.status_code == 400
    r = client.post("/api/jobs", data={"target_langs": "en-US"}, files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400
    # 2 jobs/day per client in tests: first test used one, this one uses the second, third is refused
    with SAMPLE.open("rb") as f:
        r1 = client.post("/api/jobs", data={"target_langs": "en-US"}, files={"file": ("e.json", f)})
    assert r1.status_code == 201
    with SAMPLE.open("rb") as f:
        assert client.post("/api/jobs", data={"target_langs": "en-US"}, files={"file": ("e.json", f)}).status_code == 429
    with SAMPLE.open("rb") as f:  # admin bypasses limits
        r2 = client.post("/api/jobs", data={"target_langs": "en-US"}, files={"file": ("e.json", f)},
                         headers={"x-admin-token": "test-admin"})
    assert r2.status_code == 201
    for r in (r1, r2):
        wait_ready(client, r.json()["id"])


def test_background_track_is_kept_in_the_dub(client):
    from conftest import ROOT
    import io
    import numpy as np
    import soundfile as sf
    from globaldub import audio

    buf = io.BytesIO()
    sf.write(buf, (0.1 * np.sin(2 * np.pi * 200 * np.arange(audio.SR * 4) / audio.SR)).astype(np.float32), audio.SR, format="WAV")
    with SAMPLE.open("rb") as f:
        r = client.post("/api/jobs", data={"target_langs": "en-US"}, headers={"x-admin-token": "test-admin"},
                        files={"file": ("ep.json", f), "bed": ("bed.wav", buf.getvalue(), "audio/wav")})
    assert r.status_code == 201, r.text
    job = wait_ready(client, r.json()["id"])
    assert job["background"] == "uploaded" and job["bed_url"]
    track = client.get(f"/api/jobs/{job['id']}/tracks/en-US").json()
    first = track["lines"][0]
    # the gap before the first line must contain the music bed, not silence
    from globaldub.storage import get_storage
    dub = audio.load(get_storage().path(track["dub_url"].split("/files/", 1)[1]))
    gap = dub[int(0.45 * audio.SR): int(max(0.5, first["start"] - 0.1) * audio.SR)]
    assert audio.active_rms(gap, 0.005) > 0.02


def test_same_file_as_background_is_refused(client):
    data = SAMPLE.read_bytes()
    r = client.post("/api/jobs", data={"target_langs": "en-US"}, headers={"x-admin-token": "test-admin"},
                    files={"file": ("ep.json", data), "bed": ("ep.mp3", data)})
    assert r.status_code == 400


def test_audio_upload_keeps_music_gender_and_songs(client, monkeypatch):
    """Stereo mix: male + female speech in the centre, music in the sides, then a sung phrase."""
    import io
    import os
    import numpy as np
    import soundfile as sf
    from globaldub import audio
    from test_background import _voice

    monkeypatch.setenv("GD_ASR", "mock")
    SR = audio.SR
    male = lambda t: 120 + 25 * np.sin(2 * np.pi * 1.3 * t)      # noqa: E731
    female = lambda t: 215 + 30 * np.sin(2 * np.pi * 1.1 * t)    # noqa: E731
    sung = lambda t: np.repeat([196.0, 220.0, 246.9, 220.0], int(0.5 * SR))[: len(t)]  # noqa: E731
    gap = np.zeros(int(0.7 * SR), dtype=np.float32)
    voice = np.concatenate([gap, _voice(male, 2), gap, _voice(female, 2), gap, _voice(male, 2), gap,
                            _voice(female, 2), gap, _voice(sung, 2, syllables=False), gap])
    t = np.arange(len(voice)) / SR
    music = (0.05 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
    stereo = np.stack([0.5 * voice + music, 0.5 * voice - music], axis=1)
    buf = io.BytesIO()
    sf.write(buf, stereo, SR, format="WAV")
    r = client.post("/api/jobs", data={"target_langs": "en-US"}, headers={"x-admin-token": "test-admin"},
                    files={"file": ("episode.wav", buf.getvalue(), "audio/wav")})
    assert r.status_code == 201, r.text
    job = wait_ready(client, r.json()["id"])
    assert job["status"] == "ready", job
    assert job["background"] == "separated:center"
    track = client.get(f"/api/jobs/{job['id']}/tracks/en-US").json()
    chars = {c["name"]: c["gender"] for c in track["characters"]}
    assert chars == {"Speaker 1": "male", "Speaker 2": "female"}
    lines = track["lines"]
    assert [l["speaker"] for l in lines[:4]] == ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]
    assert lines[4]["is_song"] and lines[4]["status"] == "kept"
    assert all(l["status"] != "kept" for l in lines[:4])
