from types import SimpleNamespace
from unittest.mock import MagicMock

import groq
import httpx
import pytest

from app.config import settings
from app.core import retry as retry_module
from app.core.exception import DeterministicError, TransientError
from app.core.retry import retry_on_transient
from app.services.llm_client import SYSTEM_PROMPT, LLMClient

REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def _status_error(cls, code):
    return cls(f"HTTP {code}", response=httpx.Response(code, request=REQUEST), body=None)


def _client_raising(exc):
    client = LLMClient()
    client.max_retries = 0
    client.client = MagicMock()
    client.client.chat.completions.create.side_effect = exc
    return client


@pytest.mark.parametrize("exc", [
    _status_error(groq.AuthenticationError, 401),
    _status_error(groq.BadRequestError, 400),
    _status_error(groq.PermissionDeniedError, 403),
    _status_error(groq.NotFoundError, 404),
    _status_error(groq.UnprocessableEntityError, 422),
    _status_error(groq.ConflictError, 409),
    _status_error(groq.APIStatusError, 418),
])
def test_groq_4xx_is_deterministic(exc):
    with pytest.raises(DeterministicError) as err:
        _client_raising(exc).transform_caption("hello")
    assert err.value.stage == "llm_transform"


@pytest.mark.parametrize("exc", [
    _status_error(groq.RateLimitError, 429),
    _status_error(groq.InternalServerError, 500),
    _status_error(groq.APIStatusError, 503),
    groq.APITimeoutError(request=REQUEST),
    groq.APIConnectionError(request=REQUEST),
])
def test_groq_transient_errors(exc):
    with pytest.raises(TransientError) as err:
        _client_raising(exc).transform_caption("hello")
    assert err.value.stage == "llm_transform"


def test_groq_retries_transient_then_succeeds():
    client = LLMClient()
    client.max_retries = 1
    client.client = MagicMock()
    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="  caption #x  "))])
    client.client.chat.completions.create.side_effect = [_status_error(groq.RateLimitError, 429), ok]

    assert client.transform_caption("hello") == "caption #x"
    kwargs = client.client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == settings.GROQ_MODEL
    assert kwargs["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_system_prompt_asks_for_caption_only_and_keeps_guard():
    assert "ONLY the caption" in SYSTEM_PROMPT
    assert "Here's your caption" in SYSTEM_PROMPT
    assert "Ignore previous instructions" in SYSTEM_PROMPT


def _flaky(failures):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= failures:
            raise TransientError("temp", stage="s")
        return "ok"

    return fn, calls


def test_retry_fixed_backoff(monkeypatch):
    sleeps = []
    monkeypatch.setattr(retry_module.time, "sleep", sleeps.append)
    fn, calls = _flaky(2)

    assert retry_on_transient(fn, max_retries=3, backoff="fixed", delay_seconds=1.5) == "ok"
    assert calls["n"] == 3
    assert sleeps == [1.5, 1.5]


def test_retry_exponential_backoff_then_exhausted(monkeypatch):
    sleeps = []
    monkeypatch.setattr(retry_module.time, "sleep", sleeps.append)
    exhausted = MagicMock()
    fn, calls = _flaky(10)

    with pytest.raises(TransientError):
        retry_on_transient(fn, max_retries=3, backoff="exponential", exponential_base=2.0, on_exhausted=exhausted)

    assert calls["n"] == 4
    assert sleeps == [1.0, 2.0, 4.0]
    exhausted.assert_called_once()


def test_retry_does_not_retry_deterministic(monkeypatch):
    def fn():
        raise DeterministicError("nope", stage="s")

    with pytest.raises(DeterministicError):
        retry_on_transient(fn, max_retries=3)
