"""LLM clients. All agents call `complete_json`, so swapping Gemini <-> Qwen <-> mock is one env var."""
from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from typing import Any, Callable, Optional

import httpx

from ..config import Settings

log = logging.getLogger("globaldub")


class LLMError(RuntimeError):
    pass


class RetryableError(LLMError):
    """429 / 5xx: worth waiting and trying again (possibly on the fallback model)."""

    def __init__(self, status: int, body: str, retry_after: float = 0.0):
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status, self.retry_after = status, retry_after


class NotFoundError(LLMError):
    """404: the model name is retired or not available on this key - retrying cannot help."""


class RateLimiter:
    """Process-wide request pacing shared by every thread (free tiers allow only a few requests per minute)."""

    def __init__(self, rpm: float):
        self.interval = 60.0 / max(rpm, 0.1)
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        if start > now:
            time.sleep(start - now)


_limiters: dict[str, RateLimiter] = {}
_EXHAUSTED: set[str] = set()      # models whose daily quota ran out in this process
QUOTA_WAIT_LIMIT = 120.0          # a longer requested wait means a daily quota, not a per-minute limit
_limiters_lock = threading.Lock()


def limiter(name: str, rpm: float) -> RateLimiter:
    with _limiters_lock:
        if name not in _limiters:
            _limiters[name] = RateLimiter(rpm)
        return _limiters[name]


def _retry_after(r: httpx.Response) -> float:
    """Seconds the server asked us to wait (Retry-After header or Gemini's RetryInfo.retryDelay)."""
    try:
        if r.headers.get("retry-after"):
            return float(r.headers["retry-after"])
        for d in r.json().get("error", {}).get("details", []):
            if "retryDelay" in d:
                return float(str(d["retryDelay"]).rstrip("s"))
    except Exception:
        pass
    return 0.0


def parse_json(text: str) -> Any:
    """Tolerant JSON extraction: handles ```json fences and leading chatter."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model did not return valid JSON: {text[:300]}")


class BaseLLM:
    name = "base"

    def __init__(self, settings: Settings):
        self.s = settings
        self.models_used: dict[str, int] = {}
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    # subclasses implement _raw(system, user, model) -> (text, tokens_in, tokens_out)
    def _raw(self, system: str, user: str, model: Optional[str] = None) -> tuple[str, int, int]:
        raise NotImplementedError

    fallback_model: Optional[str] = None
    primary_model: str = ""

    def complete_json(
        self,
        system: str,
        user: str,
        mock: Optional[Callable[[], Any]] = None,
        retries: int = 6,
    ) -> Any:
        last_err: Exception | None = None
        # once a model's daily quota is gone, stop knocking on it for the rest of the run
        model: Optional[str] = self.fallback_model if self.primary_model in _EXHAUSTED and self.fallback_model else None
        for attempt in range(retries):
            current = model or self.primary_model
            try:
                text, tin, tout = self._raw(system, user, model)
                self.tokens_in += tin
                self.tokens_out += tout
                self.calls += 1
                self.models_used[current] = self.models_used.get(current, 0) + 1
                return parse_json(text)
            except RetryableError as e:
                last_err = e
                if e.retry_after > QUOTA_WAIT_LIMIT:
                    _EXHAUSTED.add(current)
                    hours = e.retry_after / 3600
                    if self.fallback_model and current != self.fallback_model and self.fallback_model not in _EXHAUSTED:
                        log.warning("%s: daily quota used up on %s (resets in ~%.1fh) - switching to %s",
                                    self.name, current, hours, self.fallback_model)
                        model = self.fallback_model
                        continue
                    raise LLMError(f"{self.name}: daily free quota used up on {current} (resets in ~{hours:.1f}h). "
                                   f"Set GEMINI_MODEL to another model shown by scripts/check_setup.py, or try later.") from e
                if self.fallback_model and attempt >= 1 and current != self.fallback_model:
                    model = self.fallback_model
                    log.warning("%s: %s - switching to fallback model %s", self.name, e.status, model)
                wait = min(90.0, max(e.retry_after, 4.0 * 2**attempt)) + random.uniform(0, 2)
                log.warning("%s busy/rate-limited (%s on %s), retry %d/%d in %.0fs",
                            self.name, e.status, current, attempt + 1, retries, wait)
                time.sleep(wait)
            except NotFoundError as e:
                last_err = e
                _EXHAUSTED.add(current)
                if self.fallback_model and current != self.fallback_model and self.fallback_model not in _EXHAUSTED:
                    log.warning("%s: model %s is not available (%s) - switching to %s", self.name, current, e, self.fallback_model)
                    model = self.fallback_model
                    continue
                raise LLMError(f"{self.name}: model {current} is not available on your key. "
                               f"Run scripts/check_setup.py and set GEMINI_MODEL to a model it marks OK. ({e})") from e
            except (httpx.HTTPError, LLMError) as e:
                last_err = e
                time.sleep(2.0 * (attempt + 1))
        raise LLMError(f"{self.name} failed after {retries} attempts: {last_err}")

    def cost(self) -> float:
        return (
            self.tokens_in / 1e6 * self.s.price_llm_in_per_1m
            + self.tokens_out / 1e6 * self.s.price_llm_out_per_1m
        )


class GeminiLLM(BaseLLM):
    name = "gemini"
    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY is not set (get a free key at aistudio.google.com)")
        self.client = httpx.Client(timeout=180)
        self.fallback_model = settings.gemini_fallback_model or None
        self.primary_model = settings.gemini_model
        self.limiter = limiter("gemini", settings.llm_rpm)

    def _raw(self, system: str, user: str, model: Optional[str] = None) -> tuple[str, int, int]:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            # newer Gemini models reject temperature/top_p, so only the output format is set
            "generationConfig": {"responseMimeType": "application/json"},
        }
        self.limiter.wait()
        r = self.client.post(
            self.URL.format(model=model or self.s.gemini_model),
            headers={"x-goog-api-key": self.s.gemini_api_key},
            json=body,
        )
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(r.status_code, r.text, _retry_after(r))
        if r.status_code == 404:
            raise NotFoundError(f"Gemini HTTP 404: {r.text[:400]}")
        if r.status_code != 200:
            raise LLMError(f"Gemini HTTP {r.status_code}: {r.text[:400]}")
        data = r.json()
        cands = data.get("candidates") or []
        if not cands or "content" not in cands[0]:
            raise LLMError(f"Gemini returned no content (finishReason={cands[0].get('finishReason') if cands else 'none'})")
        parts = cands[0]["content"].get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        u = data.get("usageMetadata", {})
        tout = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
        return text, u.get("promptTokenCount", 0), tout


class OpenAICompatLLM(BaseLLM):
    """Works with OpenRouter / DashScope (Qwen) / Groq / Ollama / vLLM."""

    name = "openai"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.client = httpx.Client(timeout=180)
        self.json_mode = True
        self.primary_model = settings.openai_model
        self.limiter = limiter("openai", settings.llm_rpm)

    def _raw(self, system: str, user: str, model: Optional[str] = None) -> tuple[str, int, int]:
        body: dict[str, Any] = {
            "model": model or self.s.openai_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.s.openai_api_key}"} if self.s.openai_api_key else {}
        self.limiter.wait()
        r = self.client.post(f"{self.s.openai_base_url.rstrip('/')}/chat/completions", headers=headers, json=body)
        if r.status_code == 400 and self.json_mode:  # some servers don't support json mode
            self.json_mode = False
            return self._raw(system, user, model)
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(r.status_code, r.text, _retry_after(r))
        if r.status_code != 200:
            raise LLMError(f"LLM HTTP {r.status_code}: {r.text[:400]}")
        data = r.json()
        u = data.get("usage", {})
        return data["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


class MockLLM(BaseLLM):
    """Offline stand-in. Each agent supplies a deterministic `mock` callable for its task."""

    name = "mock"

    def complete_json(self, system: str, user: str, mock=None, retries: int = 1) -> Any:
        self.calls += 1
        # rough token estimate so the cost dashboard still has numbers to show
        self.tokens_in += (len(system) + len(user)) // 4
        out = mock() if mock else {}
        self.tokens_out += len(json.dumps(out, ensure_ascii=False)) // 4
        return out


def get_llm(settings: Settings) -> BaseLLM:
    return {"gemini": GeminiLLM, "openai": OpenAICompatLLM, "mock": MockLLM}[settings.llm_provider](settings)


def get_judge_llm(settings: Settings) -> BaseLLM:
    """The QA judge can run on a cheaper/faster model than the localizer: checking is easier than writing."""
    if settings.llm_provider == "gemini" and settings.gemini_judge_model:
        from dataclasses import replace

        return GeminiLLM(replace(settings, gemini_model=settings.gemini_judge_model))
    return get_llm(settings)
