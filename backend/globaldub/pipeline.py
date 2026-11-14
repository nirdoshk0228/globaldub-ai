r"""Orchestration.

Episode level:  ingest (ASR or script) -> character bible -> voice casting -> source clips
                -> one LangGraph run per target language, in parallel.

Track graph (LangGraph):

    localize -> qa --(high-severity flags & retries left)--> localize
                  \--> synthesize --(dub overruns slot & retries left)--> localize
                                  \--> assemble -> END
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, TypedDict

from langgraph.graph import END, StateGraph

from . import audio
from .agents.glossary import build_character_bible
from .agents.localizer import ensure_lines, localize
from .agents.qa import run_qa, set_status
from .agents.voice import assign_voices, voice_for
from .config import Settings
from .langpacks import load_pack
from .providers.asr import transcribe
from .separation import separate
from . import prosody
from .providers.llm import BaseLLM, get_judge_llm, get_llm
from .providers.tts import BaseTTS, TTSError, get_tts
from .schemas import Episode, QAFlag, StageMetric, Track
from .storage import Storage

ProgressFn = Callable[[Optional[str], str, float], None]
log = logging.getLogger("globaldub")


@dataclass
class Ctx:
    settings: Settings
    storage: Storage
    on_change: Callable[[Episode], None] = lambda ep: None   # persist the episode (DB write)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def save(self, ep: Episode) -> None:
        with self.lock:
            self.on_change(ep)

    def workdir(self, ep: Episode, *parts: str) -> Path:
        p = self.settings.work_dir / ep.id / Path(*parts) if parts else self.settings.work_dir / ep.id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def local(self, ep: Episode, key: str) -> Path:
        """Local copy of a stored object (downloads once, then cached in the work dir)."""
        p = self.settings.work_dir / ep.id / "cache" / key.replace("/", "__")
        if not p.exists():
            self.storage.get_file(key, p)
        return p


def _set_stage(ctx: Ctx, ep: Episode, track: Track, stage: str, progress: float) -> None:
    if track.stage != stage:
        log.info("[%s] %s", track.lang, stage)
    track.stage, track.progress = stage, round(progress, 3)
    ctx.save(ep)


class _Meter:
    """Measures time and incremental provider cost for a stage."""

    def __init__(self, stage: str, llm: BaseLLM | None = None, tts: BaseTTS | None = None):
        self.stage, self.llm, self.tts = stage, llm, tts

    def __enter__(self):
        self.t0 = time.time()
        self.c0 = (self.llm.cost() if self.llm else 0) + (self.tts.cost() if self.tts else 0)
        self.tok0 = (self.llm.tokens_in, self.llm.tokens_out) if self.llm else (0, 0)
        self.ch0 = self.tts.chars if self.tts else 0
        return self

    def __exit__(self, *exc):
        cost = (self.llm.cost() if self.llm else 0) + (self.tts.cost() if self.tts else 0) - self.c0
        units = {}
        if self.llm:
            units["tokens_in"] = self.llm.tokens_in - self.tok0[0]
            units["tokens_out"] = self.llm.tokens_out - self.tok0[1]
        if self.tts:
            units["tts_chars"] = self.tts.chars - self.ch0
        self.metric = StageMetric(stage=self.stage, seconds=round(time.time() - self.t0, 3),
                                  cost_usd=round(cost, 6), units=units)
        return False


def _add_metric(metrics: list[StageMetric], m: StageMetric) -> None:
    for old in metrics:
        if old.stage == m.stage:
            old.seconds = round(old.seconds + m.seconds, 3)
            old.cost_usd = round(old.cost_usd + m.cost_usd, 6)
            for k, v in m.units.items():
                old.units[k] = old.units.get(k, 0) + v
            return
    metrics.append(m)


# =============================================================== synthesis + assembly

class Synth:
    def __init__(self, ctx: Ctx, track_lang: str):
        s = ctx.settings
        self.primary = get_tts(s.tts_provider, s)
        self.fallback = get_tts(s.tts_fallback, s) if s.tts_fallback and s.tts_fallback != s.tts_provider else None
        self.lang = track_lang

    @property
    def chars(self) -> int:
        return self.primary.chars + (self.fallback.chars if self.fallback else 0)

    def cost(self) -> float:
        return self.primary.cost() + (self.fallback.cost() if self.fallback else 0)

    def say(self, ep: Episode, speaker: str, text: str, delivery, out: Path) -> float:
        for eng in [self.primary] + ([self.fallback] if self.fallback else []):
            voice, pitch = voice_for(ep, speaker, eng.name, self.lang)
            try:
                return eng.synth(text, voice, self.lang, delivery, out, pitch)
            except TTSError as e:
                last = e
        raise last


def _slot(ep: Episode, idx: int) -> float:
    return ep.slot(ep.segments[idx].id)


def synthesize_lines(ctx: Ctx, ep: Episode, track: Track, synth: Synth, only: list[str] | None = None) -> list[str]:
    """Render changed lines, fit them to the scene, flag what cannot fit. Returns ids that overran."""
    s = ctx.settings
    wd = ctx.workdir(ep, track.lang, "lines")
    overran = []
    todo = [i for i, seg in enumerate(ep.segments) if (only is None or seg.id in only)]
    for n, i in enumerate(todo):
        seg = ep.segments[i]
        ln = track.line(seg.id)
        if not ln.target_text or ln.tts_version == ln.version:
            continue
        raw = wd / f"{seg.id}_v{ln.version}.raw.wav"
        dur = synth.say(ep, seg.speaker, ln.target_text, ln.delivery, raw)
        slot = _slot(ep, i)
        final = wd / f"{seg.id}_v{ln.version}.wav"
        # a real measurement supersedes the pre-synthesis estimate
        ln.flags = [f for f in ln.flags if f.source != "audio" and f.check != "duration_overrun"]
        if dur > slot:
            factor = dur / slot
            audio.tempo(raw, final, min(factor, s.max_stretch))
            ln.stretch = round(min(factor, s.max_stretch), 3)
            if factor > s.max_stretch:
                overran.append(seg.id)
                ln.flags.append(QAFlag(check="duration_overrun", severity="high", source="audio",
                                       message=f"Dub runs {dur:.1f}s for a {slot:.1f}s slot even at {s.max_stretch}x speed.",
                                       suggestion="Shorten the line."))
        else:
            raw.replace(final)
            ln.stretch = 1.0
        raw.unlink(missing_ok=True)
        ln.tts_duration = round(audio.duration(final), 3)
        ln.tts_key = ctx.storage.put_file(final, f"episodes/{ep.id}/{track.lang}/lines/{seg.id}_v{ln.version}.wav")
        ln.tts_version = ln.version
        ln.status = "pending" if ln.status in ("auto_ok", "flagged") else ln.status
        set_status(ln)
        if n % 4 == 0:
            log.info("[%s] voiced %d/%d lines", track.lang, n + 1, len(todo))
            _set_stage(ctx, ep, track, "synthesize", 0.55 + 0.35 * (n + 1) / max(1, len(todo)))
    return overran


def assemble_track(ctx: Ctx, ep: Episode, track: Track) -> None:
    wd = ctx.workdir(ep, track.lang)
    clips, originals, mute = [], [], []
    for seg in ep.segments:
        ln = track.line(seg.id)
        if ln.tts_key and ln.target_text:
            clips.append((seg.start, str(ctx.local(ep, ln.tts_key))))
        elif seg.is_song and not ln.target_text:   # sung line: reuse the original performance
            key = f"episodes/{ep.id}/source_clips/{seg.id}.wav"
            if ctx.storage.exists(key):
                originals.append((max(0.0, seg.start - 0.05), str(ctx.local(ep, key))))
                if not ep.dialogue_key:   # the clip is the full mix, so silence the bed underneath it
                    mute.append((seg.start - 0.05, seg.end + 0.1))
    bed = ctx.local(ep, ep.bed_key) if ep.bed_key else None
    target = None
    if ep.dialogue_key:  # match the original dialogue's loudness so the dub sits in the mix the same way
        target = audio.active_rms(audio.load(ctx.local(ep, ep.dialogue_key)))
    wav = audio.assemble(clips, ep.runtime_seconds, wd / "dub.wav", bed=bed, duck_db=ctx.settings.duck_db,
                         dialogue_rms=target, loop_bed=ep.background == "uploaded",
                         raw_clips=originals, bed_mute=mute)
    stamp = int(time.time())
    mp3 = audio.to_mp3(wav, wd / f"dub_{stamp}.mp3")
    track.dub_key = ctx.storage.put_file(mp3, f"episodes/{ep.id}/{track.lang}/dub_{stamp}.mp3")
    rtl = load_pack(track.lang).direction == "rtl"
    rows = []
    for seg in ep.segments:
        text = track.line(seg.id).target_text
        if text:
            rows.append((seg.start, seg.end, ("‏" + text) if rtl else text))
    srt = audio.write_srt(rows, wd / f"subs_{stamp}.srt")
    track.srt_key = ctx.storage.put_file(srt, f"episodes/{ep.id}/{track.lang}/subs_{stamp}.srt")


# =============================================================== track graph (LangGraph)

class TrackState(TypedDict, total=False):
    round: int
    pending: list[str]                 # seg ids to (re)localize this round
    feedback: dict[str, list[str]]     # seg id -> problems to fix
    changed: list[str]                 # seg ids changed this round (need QA + TTS)


def build_track_graph(ctx: Ctx, ep: Episode, track: Track, llm: BaseLLM, synth: Synth, judge: BaseLLM | None = None):
    s = ctx.settings
    judge = judge or llm

    def n_localize(st: TrackState) -> TrackState:
        _set_stage(ctx, ep, track, "localize" if st["round"] == 0 else f"retry {st['round']}", 0.1 + 0.1 * st["round"])
        with _Meter("localize", llm=llm) as m:
            changed = localize(ep, track, st["pending"], llm, s, st.get("feedback"))
        _add_metric(track.metrics, m.metric)
        return {"changed": changed}

    def n_qa(st: TrackState) -> TrackState:
        _set_stage(ctx, ep, track, "qa", 0.35 + 0.05 * st["round"])
        with _Meter("qa", llm=judge) as m:
            run_qa(ep, track, st["changed"], judge, s)
        _add_metric(track.metrics, m.metric)
        def needs_rewrite(ln) -> bool:
            if ln.status in ("approved", "edited") or ln.seg_id not in st["changed"]:
                return False
            if ln.max_severity == "high":
                return ln.attempts <= s.max_retries
            # a medium judge finding with a concrete fix gets one automatic rewrite before reaching a human
            return ln.attempts <= 1 and any(f.severity == "medium" and f.source == "llm" and f.suggestion for f in ln.flags)

        retry = [ln.seg_id for ln in track.lines if needs_rewrite(ln)]
        fb = {sid: [f"{f.check}: {f.message} {f.suggestion}".strip() for f in track.line(sid).flags
                    if f.severity in ("high", "medium")] for sid in retry}
        return {"pending": retry, "feedback": fb}

    def route_after_qa(st: TrackState) -> str:
        if st["pending"] and st["round"] < s.max_retries:
            return "bump"
        return "synthesize"

    def n_bump(st: TrackState) -> TrackState:
        return {"round": st["round"] + 1}

    def n_synthesize(st: TrackState) -> TrackState:
        _set_stage(ctx, ep, track, "synthesize", 0.55)
        with _Meter("tts", tts=synth) as m:  # type: ignore[arg-type]
            over = synthesize_lines(ctx, ep, track, synth)
        _add_metric(track.metrics, m.metric)
        retry = [sid for sid in over if track.line(sid).attempts <= s.max_retries
                 and track.line(sid).status not in ("approved", "edited")]
        fb = {sid: [f.message + " " + f.suggestion for f in track.line(sid).flags if f.source == "audio"] for sid in retry}
        return {"pending": retry, "feedback": fb}

    def route_after_tts(st: TrackState) -> str:
        if st["pending"] and st["round"] < s.max_retries:
            return "bump"
        return "assemble"

    def n_assemble(st: TrackState) -> TrackState:
        _set_stage(ctx, ep, track, "assemble", 0.92)
        with _Meter("assemble") as m:
            assemble_track(ctx, ep, track)
        _add_metric(track.metrics, m.metric)
        return {}

    g = StateGraph(TrackState)
    g.add_node("localize", n_localize)
    g.add_node("qa", n_qa)
    g.add_node("bump", n_bump)
    g.add_node("synthesize", n_synthesize)
    g.add_node("assemble", n_assemble)
    g.set_entry_point("localize")
    g.add_edge("localize", "qa")
    g.add_conditional_edges("qa", route_after_qa, {"bump": "bump", "synthesize": "synthesize"})
    g.add_edge("bump", "localize")
    g.add_conditional_edges("synthesize", route_after_tts, {"bump": "bump", "assemble": "assemble"})
    g.add_edge("assemble", END)
    return g.compile()


def run_track(ctx: Ctx, ep: Episode, lang: str) -> Track:
    track = ep.tracks[lang]
    track.status, track.error = "running", None
    ensure_lines(ep, track)
    llm = get_llm(ctx.settings)
    synth = Synth(ctx, lang)
    try:
        graph = build_track_graph(ctx, ep, track, llm, synth, get_judge_llm(ctx.settings))
        pending = [ln.seg_id for ln in track.lines if ln.status not in ("approved", "edited", "kept")]
        graph.invoke({"round": 0, "pending": pending, "feedback": {}, "changed": []},
                     {"recursion_limit": 50})
        track.status, track.stage, track.progress = "ready", "done", 1.0
        log.info("[%s] done", lang)
    except Exception as e:  # one language failing must not kill the others
        track.status, track.error = "failed", f"{type(e).__name__}: {e}"
        log.exception("[%s] failed", lang)
    ctx.save(ep)
    return track


# =============================================================== episode level

def render_source_audio(ctx: Ctx, ep: Episode) -> None:
    """Script-only uploads have no original audio; voice the source so reviewers can still A/B lines."""
    s = ctx.settings
    assign_voices(ep, s.tts_provider, [ep.source_lang])
    synth = Synth(ctx, ep.source_lang)
    wd = ctx.workdir(ep, "source")
    clips, t = [], 0.6
    for seg in ep.segments:
        from .schemas import Delivery

        p = wd / f"{seg.id}.wav"
        log.info("voicing original %s line %d/%d", ep.source_lang, len(clips) + 1, len(ep.segments))
        dur = synth.say(ep, seg.speaker, seg.text, Delivery(emotion=seg.emotion, intensity=0.6), p)
        t += 0.35 if seg.emotion not in ("shocked", "sad", "broken", "mysterious") else 0.8
        seg.start, seg.end = round(t, 3), round(t + dur, 3)
        clips.append((seg.start, str(p)))
        t = seg.end
    dialogue = audio.assemble(clips, ep.runtime_seconds, wd / "dialogue.wav")
    ep.dialogue_key = ctx.storage.put_file(dialogue, f"episodes/{ep.id}/dialogue.wav")
    if ep.bed_key:
        src = audio.assemble(clips, ep.runtime_seconds, wd / "source.wav", bed=ctx.local(ep, ep.bed_key),
                             duck_db=s.duck_db, loop_bed=True)
    else:
        src = dialogue
    ep.source_audio_key = ctx.storage.put_file(src, f"episodes/{ep.id}/source.wav")
    ep.ingest_metrics.append(StageMetric(stage="source_voicing", units={"tts_chars": synth.chars},
                                         cost_usd=round(synth.cost(), 6)))


def cut_source_clips(ctx: Ctx, ep: Episode) -> None:
    src = ctx.local(ep, ep.dialogue_key or ep.source_audio_key)
    wd = ctx.workdir(ep, "source_clips")
    for seg in ep.segments:
        key = f"episodes/{ep.id}/source_clips/{seg.id}.wav"
        if ctx.storage.exists(key):
            continue
        p = audio.cut(src, max(0, seg.start - 0.05), seg.end + 0.1, wd / f"{seg.id}.wav")
        ctx.storage.put_file(p, key)


def prepare_background(ctx: Ctx, ep: Episode) -> None:
    """Get a music & effects bed: an uploaded M&E track, or one separated from the uploaded mix."""
    s = ctx.settings
    if ep.bed_key:
        return
    wd = ctx.workdir(ep, "stems")
    if ep.bed_upload_key:
        bed = audio.to_wav(ctx.local(ep, ep.bed_upload_key), wd / "bed.wav")
        ep.bed_key = ctx.storage.put_file(bed, f"episodes/{ep.id}/bed.wav")
        ep.background = "uploaded"
        log.info("using the uploaded background track")
        return
    if not ep.source_audio_key or ep.segments:   # script uploads have no mix to separate
        return
    t0 = time.time()
    stems = separate(ctx.local(ep, ep.source_audio_key), wd, s.separation, s.demucs_model)
    if stems is None:
        ep.background = "none"
        return
    ep.bed_key = ctx.storage.put_file(stems.bed, f"episodes/{ep.id}/bed.wav")
    if stems.dialogue:
        ep.dialogue_key = ctx.storage.put_file(stems.dialogue, f"episodes/{ep.id}/dialogue.wav")
    ep.background = f"separated:{stems.method}"
    ep.ingest_metrics.append(StageMetric(stage=f"separation_{stems.method}", seconds=round(time.time() - t0, 3)))
    log.info("background separated with %s in %.0fs", stems.method, time.time() - t0)


def ingest(ctx: Ctx, ep: Episode) -> None:
    s = ctx.settings
    prepare_background(ctx, ep)
    t0 = time.time()
    if not ep.segments:
        if not ep.source_audio_key:
            raise ValueError("Episode has neither a script nor source audio")
        src = ctx.local(ep, ep.source_audio_key)
        speech = ctx.local(ep, ep.dialogue_key) if ep.dialogue_key else src   # cleaner transcription without music
        ep.segments = transcribe(speech, ep.source_lang, s)
        info = prosody.apply(ep, audio.load(speech))     # genders, speakers by pitch, sung lines, delivery hints
        ep.ingest_metrics.append(StageMetric(stage="audio_analysis", units={k: float(v) for k, v in info.items()}))
        minutes = audio.duration(audio.to_wav(src, src.with_suffix(".probe.wav"))) / 60
        ep.ingest_metrics.append(StageMetric(stage="asr", seconds=round(time.time() - t0, 3),
                                             cost_usd=round(minutes * s.price_asr_per_min, 6),
                                             units={"audio_minutes": round(minutes, 3)}))
    llm = get_llm(s)
    log.info("building character bible")
    with _Meter("character_bible", llm=llm) as m:
        build_character_bible(ep, llm)
    ep.ingest_metrics.append(m.metric)
    if not ep.source_audio_key:
        render_source_audio(ctx, ep)
    assign_voices(ep, s.tts_provider, ep.target_langs)
    if s.tts_fallback:
        assign_voices(ep, s.tts_fallback, ep.target_langs)
    cut_source_clips(ctx, ep)
    for lang in ep.target_langs:
        ep.tracks.setdefault(lang, Track(lang=lang))
    ctx.save(ep)


def run_episode(ctx: Ctx, ep: Episode, langs: list[str] | None = None) -> Episode:
    ingest(ctx, ep)
    langs = langs or ep.target_langs
    with ThreadPoolExecutor(max_workers=max(1, ctx.settings.parallel_tracks)) as pool:
        futures = [pool.submit(run_track, ctx, ep, lang) for lang in langs]
        for f in as_completed(futures):
            f.result()
    return ep


def rerender_line(ctx: Ctx, ep: Episode, lang: str, seg_id: str) -> None:
    """After a human edit: re-check, re-voice and re-assemble only what changed."""
    track = ep.tracks[lang]
    run_qa(ep, track, [seg_id], get_judge_llm(ctx.settings), ctx.settings)
    synth = Synth(ctx, lang)
    with _Meter("tts", tts=synth) as m:  # type: ignore[arg-type]
        synthesize_lines(ctx, ep, track, synth, only=[seg_id])
    _add_metric(track.metrics, m.metric)
    assemble_track(ctx, ep, track)
    ctx.save(ep)
