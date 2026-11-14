"""Setup doctor: checks FFmpeg, .env, Gemini, Groq and the TTS engines with one tiny request each.

    python scripts/check_setup.py
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from globaldub.config import BACKEND_ROOT, get_settings  # noqa: E402

OK, BAD, WARN = "[ OK ]", "[FAIL]", "[WARN]"


def line(tag: str, what: str, detail: str = "") -> None:
    print(f"{tag} {what}" + (f"\n       {detail}" if detail else ""))


def short(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return f"HTTP {r.status_code}: {(msg or r.text)[:220]}"
    except Exception:
        return f"HTTP {r.status_code}: {r.text[:220]}"


def check_gemini_model(s, model: str) -> bool:
    t0 = time.time()
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": s.gemini_api_key},
        json={"contents": [{"role": "user", "parts": [{"text": 'Reply with JSON {"ok": true}'}]}],
              "generationConfig": {"responseMimeType": "application/json"}},
        timeout=90,
    )
    if r.status_code == 200:
        line(OK, f"Gemini model {model}", f"answered in {time.time() - t0:.1f}s")
        return True
    hint = {
        429: "Rate limit or quota. Free tier allows only a few requests per minute/day. Lower GD_LLM_RPM, wait, or check limits in AI Studio.",
        404: "Model name not available for your key. Pick a model listed in AI Studio and set GEMINI_MODEL.",
        400: "Bad request - often an invalid API key or a model that rejects JSON mode.",
        403: "Key not allowed - check the key in AI Studio and that the Generative Language API is enabled.",
    }.get(r.status_code, "Server-side problem; try again in a minute.")
    line(BAD, f"Gemini model {model}", f"{short(r)}\n       -> {hint}")
    return False


def main() -> None:
    s = get_settings()
    print(f"\nGlobalDub setup check   (backend: {BACKEND_ROOT})\n")

    env = BACKEND_ROOT / ".env"
    line(OK if env.exists() else BAD, ".env file", str(env) if env.exists() else "Not found - copy .env.example to .env")
    line(OK, f"Providers: llm={s.llm_provider}  tts={s.tts_provider}  fallback={s.tts_fallback or '-'}  asr={s.asr_provider}",
         f"LLM pacing: {s.llm_rpm:g} requests/min, {s.batch_size} lines per call")

    for exe in ("ffmpeg", "ffprobe"):
        p = shutil.which(exe)
        line(OK if p else BAD, exe, p or "Not on PATH - install FFmpeg and reopen the terminal")

    # ---- Gemini
    if s.gemini_api_key:
        r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                      headers={"x-goog-api-key": s.gemini_api_key}, timeout=30)
        if r.status_code == 200:
            names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                     if "generateContent" in m.get("supportedGenerationMethods", [])]
            flash = [n for n in names if "flash" in n and "tts" not in n and "image" not in n][:8]
            line(OK, "Gemini API key accepted", "text models on your key include: " + ", ".join(flash))
            check_gemini_model(s, s.gemini_model)
            if s.gemini_fallback_model:
                check_gemini_model(s, s.gemini_fallback_model)
        else:
            line(BAD, "Gemini API key", short(r))
    else:
        line(WARN if s.llm_provider != "gemini" else BAD, "GEMINI_API_KEY", "not set")

    # ---- Groq
    if s.groq_api_key:
        r = httpx.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {s.groq_api_key}"}, timeout=30)
        line(OK if r.status_code == 200 else BAD, "Groq API key (audio transcription)", "" if r.status_code == 200 else short(r))
    else:
        line(WARN, "GROQ_API_KEY not set", "Only needed when you upload audio instead of a script.")

    # ---- Edge TTS
    if "edge" in (s.tts_provider, s.tts_fallback):
        try:
            import edge_tts

            out = Path(tempfile.gettempdir()) / "gd_edge_check.mp3"

            async def go():
                await edge_tts.Communicate("नमस्ते, यह एक परीक्षण है।", "hi-IN-SwaraNeural").save(str(out))
                await edge_tts.Communicate("வணக்கம்", "ta-IN-PallaviNeural").save(str(out))

            t0 = time.time()
            asyncio.run(go())
            line(OK, "Edge TTS (Hindi + Tamil voices)", f"{time.time() - t0:.1f}s")
        except Exception as e:
            line(BAD, "Edge TTS", f"{type(e).__name__}: {e}")

    # ---- Gemini TTS
    if "gemini" in (s.tts_provider, s.tts_fallback) and s.gemini_api_key:
        r = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{s.gemini_tts_model}:generateContent",
            headers={"x-goog-api-key": s.gemini_api_key},
            json={"contents": [{"parts": [{"text": "Say: hello"}]}],
                  "generationConfig": {"responseModalities": ["AUDIO"],
                                       "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}}},
            timeout=90,
        )
        line(OK if r.status_code == 200 else WARN, f"Gemini TTS ({s.gemini_tts_model}, fallback voice engine)",
             "" if r.status_code == 200 else short(r) + "\n       -> only matters if Edge TTS fails")
    print()


if __name__ == "__main__":
    main()
