"""GlobalDub AI - HTTP API."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from globaldub.config import BACKEND_ROOT, get_settings
from globaldub.episodes import AUDIO_SUFFIXES, SCRIPT_SUFFIXES, episode_from_audio, episode_from_script, new_id, track_summary
from globaldub.langpacks import available_targets, load_pack
from globaldub.schemas import Episode
from globaldub.storage import LocalStorage, get_storage

from . import repo
from .db import Job, Review, init_db, jobs_today_for, session, spend_today
from .queue import enqueue, queue_depth
from .settings import app_settings

logging.basicConfig(level=logging.INFO, format='{"t":"%(asctime)s","lvl":"%(levelname)s","log":"%(name)s","msg":"%(message)s"}')
log = logging.getLogger("globaldub.api")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="GlobalDub AI", version="1.0.0", lifespan=lifespan,
              description="Agentic multilingual dubbing + localization QA for Indian audio drama.")


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:10]
    t0 = time.time()
    try:
        resp = await call_next(request)
    except Exception:
        log.exception("unhandled error rid=%s path=%s", rid, request.url.path)
        resp = JSONResponse({"detail": "Internal error", "request_id": rid}, status_code=500)
    resp.headers["x-request-id"] = rid
    if not request.url.path.startswith("/files"):
        log.info("rid=%s %s %s %s %.0fms", rid, request.method, request.url.path, resp.status_code, (time.time() - t0) * 1000)
    return resp


# added last so it is the outermost layer: even 500 responses carry CORS headers and the browser can show them
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(app_settings().cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ helpers

def _client_hash(request: Request) -> str:
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "")).split(",")[0].strip()
    return hashlib.sha256((ip + app_settings().ip_salt).encode()).hexdigest()


def _is_admin(token: Optional[str]) -> bool:
    adm = app_settings().admin_token
    return bool(adm and token and hmac.compare_digest(adm, token))


def _job_or_404(job_id: str) -> Job:
    with session() as s:
        job = s.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _can_edit(job: Job, edit_token: Optional[str], admin_token: Optional[str]) -> None:
    if _is_admin(admin_token):
        return
    if not edit_token or not hmac.compare_digest(job.edit_token_hash, hashlib.sha256(edit_token.encode()).hexdigest()):
        raise HTTPException(403, "This job can only be edited from the browser that created it.")


def _url(key: Optional[str]) -> Optional[str]:
    return get_storage().url(key)


def _job_summary(job: Job) -> dict:
    ep = Episode.model_validate(job.episode)
    return {
        "id": job.id,
        "title": job.title,
        "status": job.status,
        "error": job.error,
        "is_demo": job.is_demo,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "source_lang": ep.source_lang,
        "target_langs": ep.target_langs,
        "runtime_seconds": round(ep.runtime_seconds, 1),
        "lines": len(ep.segments),
        "synopsis": ep.synopsis,
        "source_audio_url": _url(ep.source_audio_key),
        "background": ep.background,
        "bed_url": _url(ep.bed_key),
        "cost_usd": job.cost_usd,
        "tracks": [
            {**{k: v for k, v in track_summary(ep, lang).items() if k != "metrics"},
             "dub_url": _url(ep.tracks[lang].dub_key), "srt_url": _url(ep.tracks[lang].srt_key)}
            for lang in ep.target_langs if lang in ep.tracks
        ],
    }


# ------------------------------------------------------------------ meta

@app.get("/api/health")
def health():
    with session() as s:
        s.execute(select(1))
    ps = get_settings()
    return {"ok": True, "llm": ps.llm_provider, "tts": ps.tts_provider, "asr": ps.asr_provider,
            "queue_depth": queue_depth(), "storage": type(get_storage()).__name__}


@app.get("/api/languages")
def languages():
    return [{"code": p.code, "name": p.name, "native_name": p.native_name, "market": p.market,
             "script": p.script, "direction": p.direction} for p in available_targets()]


@app.get("/api/limits")
def limits(request: Request):
    a = app_settings()
    return {"max_upload_mb": a.max_upload_mb, "max_audio_seconds": a.max_audio_seconds,
            "max_langs_per_job": a.max_langs_per_job, "jobs_per_day": a.jobs_per_ip_per_day,
            "jobs_used_today": jobs_today_for(_client_hash(request)),
            "budget_left_today": spend_today() < a.daily_budget_usd}


@app.get("/api/benchmark")
def benchmark():
    p = BACKEND_ROOT / "eval" / "results"
    out = {}
    for mode in ("rules", "judge"):
        f = p / f"latest_{mode}.json"
        if f.exists():
            out[mode] = json.loads(f.read_text())
    return out


# ------------------------------------------------------------------ jobs

def _probe_seconds(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise HTTPException(400, "Could not read this audio file.")


@app.post("/api/jobs", status_code=201)
async def create_job(
    request: Request,
    file: UploadFile = File(...),
    target_langs: str = Form(...),
    bed: Optional[UploadFile] = File(None),
    title: str = Form(""),
    source_lang: str = Form("hi-IN"),
    x_admin_token: Optional[str] = Header(None),
):
    a = app_settings()
    admin = _is_admin(x_admin_token)
    client = _client_hash(request)
    langs = list(dict.fromkeys(x.strip() for x in target_langs.split(",") if x.strip()))
    valid = {p.code for p in available_targets()}
    bad = [x for x in langs if x not in valid]
    if not langs or bad:
        raise HTTPException(400, f"Unknown target language(s): {bad or 'none given'}")
    try:
        load_pack(source_lang)
    except KeyError:
        raise HTTPException(400, f"Unsupported source language {source_lang}")
    if not admin:
        if len(langs) > a.max_langs_per_job:
            raise HTTPException(400, f"The public demo allows up to {a.max_langs_per_job} languages per job.")
        if jobs_today_for(client) >= a.jobs_per_ip_per_day:
            raise HTTPException(429, f"Daily demo limit reached ({a.jobs_per_ip_per_day} jobs). Try again tomorrow or browse the gallery.")
        if spend_today() >= a.daily_budget_usd:
            raise HTTPException(429, "Today's demo budget is used up. The gallery episodes are still available.")

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in AUDIO_SUFFIXES | SCRIPT_SUFFIXES:
        raise HTTPException(400, f"Upload audio ({', '.join(sorted(AUDIO_SUFFIXES))}) or a script (.json/.srt/.txt).")

    bed_suffix = Path(bed.filename or "").suffix.lower() if bed is not None and bed.filename else ""
    if bed_suffix and bed_suffix not in AUDIO_SUFFIXES:
        raise HTTPException(400, "The background track must be an audio file (.mp3, .wav, .m4a ...).")

    tmpdir = Path(tempfile.mkdtemp(prefix="gd-up-"))
    try:
        limit = a.max_upload_mb * 1024 * 1024

        async def save(up: UploadFile, dest: Path) -> None:
            size = 0
            with dest.open("wb") as out:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"Each file must be under {a.max_upload_mb} MB.")
                    out.write(chunk)

        tmp = tmpdir / f"upload{suffix}"
        await save(file, tmp)
        bed_tmp = None
        if bed_suffix:
            bed_tmp = tmpdir / f"bed{bed_suffix}"
            await save(bed, bed_tmp)
            if bed_tmp.stat().st_size == tmp.stat().st_size and bed_tmp.read_bytes() == tmp.read_bytes():
                raise HTTPException(400, "The background track is the same file as the episode. Upload only the music "
                                         "and effects there, or leave it empty to separate the background automatically.")
            secs = _probe_seconds(bed_tmp)
            if not admin and secs > a.max_audio_seconds * 2:
                raise HTTPException(400, f"Background track is {secs:.0f}s; keep it under {a.max_audio_seconds * 2}s.")

        job_id = new_id()
        if suffix in SCRIPT_SUFFIXES:
            try:
                ep = episode_from_script(tmp, langs, title or None, source_lang, ep_id=job_id)
            except Exception as e:
                raise HTTPException(400, f"Could not parse script: {e}")
            if not ep.segments:
                raise HTTPException(400, "No dialogue lines found in the script.")
            if not admin and len(ep.segments) > a.max_script_lines:
                raise HTTPException(400, f"The public demo accepts scripts up to {a.max_script_lines} lines.")
        else:
            secs = _probe_seconds(tmp)
            if not admin and secs > a.max_audio_seconds:
                raise HTTPException(400, f"Audio is {secs:.0f}s; the public demo accepts up to {a.max_audio_seconds}s.")
            try:
                key = get_storage().put_file(tmp, f"episodes/{job_id}/upload{suffix}")
            except Exception as e:
                log.exception("storage upload failed")
                raise HTTPException(502, f"Could not save the file to storage ({type(e).__name__}). Check the S3_* settings.")
            ep = episode_from_audio(key, langs, title or Path(file.filename or "episode").stem, source_lang, ep_id=job_id)
        if bed_tmp is not None:
            ep.bed_upload_key = get_storage().put_file(bed_tmp, f"episodes/{job_id}/bed_upload{bed_suffix}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    token = secrets.token_urlsafe(24)
    with session() as s:
        s.add(Job(id=ep.id, title=ep.title[:200], source_lang=ep.source_lang, target_langs=langs,
                  status="queued", episode=ep.model_dump(mode="json"),
                  edit_token_hash=hashlib.sha256(token.encode()).hexdigest(), client_hash=client))
    enqueue("run_job", ep.id)
    log.info("job %s created langs=%s lines=%d", ep.id, langs, len(ep.segments))
    return {"id": ep.id, "edit_token": token}


@app.get("/api/jobs")
def list_jobs(ids: str = ""):
    """Jobs by id (the browser keeps its own list in localStorage); there is no public list of all jobs."""
    wanted = [x for x in ids.split(",") if x][:50]
    if not wanted:
        return []
    with session() as s:
        jobs = s.scalars(select(Job).where(Job.id.in_(wanted)).order_by(Job.created_at.desc())).all()
    return [_job_summary(j) for j in jobs]


@app.get("/api/gallery")
def gallery():
    with session() as s:
        jobs = s.scalars(select(Job).where(Job.is_demo.is_(True), Job.status.in_(["ready", "partial"]))
                         .order_by(Job.created_at.desc()).limit(20)).all()
    return [_job_summary(j) for j in jobs]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _job_summary(_job_or_404(job_id))


@app.get("/api/jobs/{job_id}/tracks/{lang}")
def get_track(job_id: str, lang: str):
    job = _job_or_404(job_id)
    ep = Episode.model_validate(job.episode)
    if lang not in ep.tracks:
        raise HTTPException(404, "Track not found")
    t = ep.tracks[lang]
    pack = load_pack(lang)
    lines = []
    for seg in ep.segments:
        try:
            ln = t.line(seg.id)
        except KeyError:
            continue
        lines.append({
            "seg_id": seg.id, "start": seg.start, "end": seg.end, "speaker": seg.speaker,
            "source_text": seg.text, "source_emotion": seg.emotion, "is_song": seg.is_song, "audio_hint": seg.audio_hint,
            "source_clip_url": _url(f"episodes/{ep.id}/source_clips/{seg.id}.wav") if ep.source_audio_key else None,
            "target_text": ln.target_text, "back_translation": ln.back_translation,
            "adaptation_note": ln.adaptation_note, "delivery": ln.delivery.model_dump(),
            "flags": [f.model_dump() for f in ln.flags], "status": ln.status, "attempts": ln.attempts,
            "version": ln.version, "tts_version": ln.tts_version, "rendering": ln.tts_version != ln.version and bool(ln.target_text),
            "tts_url": _url(ln.tts_key), "tts_duration": ln.tts_duration, "stretch": ln.stretch,
        })
    chars = [{"name": c.name, "name_in_target": c.target_names.get(lang, c.name), "gender": c.gender, "age": c.age,
              "persona": c.persona, "voice": next((v for k, v in c.voices.items() if k.endswith(":" + lang)), None)}
             for c in ep.glossary.characters]
    return {"job_id": ep.id, "title": ep.title, "lang": lang, "language": pack.name, "direction": pack.direction,
            "summary": track_summary(ep, lang), "dub_url": _url(t.dub_key), "srt_url": _url(t.srt_key),
            "source_audio_url": _url(ep.source_audio_key), "characters": chars, "lines": lines}


class LineAction(BaseModel):
    action: Literal["approve", "edit", "reopen"]
    target_text: Optional[str] = Field(None, max_length=600)
    seconds_spent: float = Field(0, ge=0, le=3600)


@app.patch("/api/jobs/{job_id}/tracks/{lang}/lines/{seg_id}")
def act_on_line(job_id: str, lang: str, seg_id: str, body: LineAction,
                x_edit_token: Optional[str] = Header(None), x_admin_token: Optional[str] = Header(None)):
    job = _job_or_404(job_id)
    _can_edit(job, x_edit_token, x_admin_token)
    a = app_settings()
    if body.action == "edit" and not _is_admin(x_admin_token) and job.edits >= a.edits_per_job:
        raise HTTPException(429, "Edit limit reached for this demo job.")
    result: dict = {}

    def apply(ep: Episode, j: Job) -> None:
        if lang not in ep.tracks:
            raise HTTPException(404, "Track not found")
        t = ep.tracks[lang]
        if t.status not in ("ready", "failed"):
            raise HTTPException(409, "This language is still being processed.")
        ln = t.line(seg_id)
        before = ln.target_text
        checks = sorted({f.check for f in ln.flags if f.severity in ("medium", "high")})
        if body.action == "approve":
            ln.status = "approved"
        elif body.action == "reopen":
            ln.status = "flagged" if ln.max_severity in ("medium", "high") else "auto_ok"
        else:
            text = (body.target_text or "").strip()
            if not text:
                raise HTTPException(400, "target_text is required for edit")
            if text != ln.target_text:
                ln.target_text, ln.version, ln.back_translation = text, ln.version + 1, ""
                ln.adaptation_note = "Edited by reviewer"
                j.edits += 1
                result["rerender"] = True
            ln.status = "edited"
        with session() as s:
            s.add(Review(job_id=job_id, lang=lang, seg_id=seg_id, action=body.action, before_text=before,
                         after_text=ln.target_text, flag_checks=checks, seconds_spent=body.seconds_spent))
        result["line"] = {"seg_id": seg_id, "status": ln.status, "version": ln.version, "target_text": ln.target_text}

    try:
        repo.update(job_id, apply)
    except KeyError:
        raise HTTPException(404, "Line not found")
    if result.get("rerender"):
        enqueue("rerender", job_id, lang, seg_id, timeout=300)
    return result


@app.get("/api/jobs/{job_id}/report")
def report(job_id: str):
    job = _job_or_404(job_id)
    ep = Episode.model_validate(job.episode)
    with session() as s:
        rows = s.execute(select(Review.lang, Review.action, func.count(), func.coalesce(func.sum(Review.seconds_spent), 0))
                         .where(Review.job_id == job_id).group_by(Review.lang, Review.action)).all()
    review: dict = {}
    for lang, action, n, secs in rows:
        r = review.setdefault(lang, {"approve": 0, "edit": 0, "reopen": 0, "seconds": 0.0})
        r[action] = n
        r["seconds"] = round(r["seconds"] + float(secs), 1)
    return {
        "job_id": job_id, "title": ep.title, "runtime_seconds": round(ep.runtime_seconds, 1),
        "ingest": [m.model_dump() for m in ep.ingest_metrics],
        "tracks": [track_summary(ep, lang) for lang in ep.target_langs if lang in ep.tracks],
        "review": review, "total_cost_usd": job.cost_usd,
    }


# ------------------------------------------------------------------ admin

@app.post("/api/admin/jobs/{job_id}/demo")
def set_demo(job_id: str, is_demo: bool = True, x_admin_token: Optional[str] = Header(None)):
    if not _is_admin(x_admin_token):
        raise HTTPException(403, "admin only")
    with session() as s:
        job = s.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        job.is_demo = is_demo
    return {"id": job_id, "is_demo": is_demo}


@app.post("/api/admin/jobs/{job_id}/retry")
def retry_job(job_id: str, x_admin_token: Optional[str] = Header(None)):
    if not _is_admin(x_admin_token):
        raise HTTPException(403, "admin only")

    def fn(ep: Episode, j: Job):
        for t in ep.tracks.values():
            if t.status == "failed":
                t.status, t.error = "queued", None
        j.status, j.error = "queued", None
    repo.update(job_id, fn)
    enqueue("run_job", job_id)
    return {"id": job_id, "status": "queued"}


# ------------------------------------------------------------------ local file serving (dev only; R2 serves signed URLs)

@app.get("/files/{key:path}")
def files(key: str):
    st = get_storage()
    if not isinstance(st, LocalStorage):
        raise HTTPException(404)
    try:
        p = st.path(key)
    except ValueError:
        raise HTTPException(400)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)
