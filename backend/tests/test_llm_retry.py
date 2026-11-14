import json

import httpx

from globaldub.config import get_settings
from globaldub.providers import llm as llm_mod


def test_gemini_rate_limit_then_fallback_then_success(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        model = req.url.path.split("/models/")[1].split(":")[0]
        calls.append(model)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "message": "quota", "details": [
                {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}})
        if len(calls) == 2:
            return httpx.Response(503, json={"error": {"code": 503, "message": "overloaded"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({"ok": True})}]}}],
                                         "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3}})

    s = get_settings(gemini_api_key="k", gemini_model="primary-model", gemini_fallback_model="backup-model", llm_rpm=6000)
    g = llm_mod.GeminiLLM(s)
    g.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert g.complete_json("sys", "user") == {"ok": True}
    assert calls == ["primary-model", "primary-model", "backup-model"]
    assert g.tokens_in == 10


def test_retry_after_parsing():
    r = httpx.Response(429, json={"error": {"details": [{"retryDelay": "37s"}]}})
    assert llm_mod._retry_after(r) == 37.0
    assert llm_mod._retry_after(httpx.Response(503, headers={"retry-after": "5"})) == 5.0


def test_gives_clear_error_after_retries(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    s = get_settings(gemini_api_key="k", llm_rpm=6000)
    g = llm_mod.GeminiLLM(s)
    g.client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"error": {"message": "Quota exceeded for metric"}})))
    try:
        g.complete_json("s", "u", retries=2)
    except llm_mod.LLMError as e:
        assert "429" in str(e) and "Quota exceeded" in str(e)
    else:
        raise AssertionError("expected LLMError")


def test_daily_quota_switches_to_fallback_without_long_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: slept.append(s))
    llm_mod._EXHAUSTED.clear()
    calls = []

    def handler(req):
        model = req.url.path.split("/models/")[1].split(":")[0]
        calls.append(model)
        if model == "big-model":
            return httpx.Response(429, json={"error": {"details": [{"retryDelay": "37077s"}]}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})

    s = get_settings(gemini_api_key="k", gemini_model="big-model", gemini_fallback_model="lite-model", llm_rpm=6000)
    g = llm_mod.GeminiLLM(s)
    g.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert g.complete_json("s", "u") == {"ok": 1}
    assert g.complete_json("s", "u") == {"ok": 1}          # second call goes straight to the fallback
    assert calls == ["big-model", "lite-model", "lite-model"]
    assert all(x < 100 for x in slept)
    llm_mod._EXHAUSTED.clear()


def test_retired_model_switches_to_fallback(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    llm_mod._EXHAUSTED.clear()
    calls = []

    def handler(req):
        model = req.url.path.split("/models/")[1].split(":")[0]
        calls.append(model)
        if model == "old-model":
            return httpx.Response(404, json={"error": {"message": "no longer available"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})

    s = get_settings(gemini_api_key="k", gemini_model="old-model", gemini_fallback_model="lite-model", llm_rpm=6000)
    g = llm_mod.GeminiLLM(s)
    g.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert g.complete_json("s", "u") == {"ok": 1}
    assert calls == ["old-model", "lite-model"]
    llm_mod._EXHAUSTED.clear()
