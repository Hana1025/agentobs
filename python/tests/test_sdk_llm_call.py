from __future__ import annotations

import time

import pytest

import agentobs as ao
from agentobs.core import attrs, llm
from tests.conftest import finished_spans


class _Resp:
    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}


class HttpError(Exception):
    def __init__(self, status: int, headers: dict | None = None):
        super().__init__(f"http {status}")
        self.response = _Resp(status, headers)


def _ok(text: str = '{"a": 1}', model: str = "mock/fast"):
    return {
        "model": model,
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm, "backoff_s", lambda *a, **k: 0.0)


def test_success_fills_attributes_and_cost(sdk):
    with ao.job("j"), ao.stage("extract"):
        r = ao.llm_call(lambda t: _ok(), provider="mock", model="mock/fast", parse=ao.parsers.json_object)
    assert r.output == {"a": 1}
    assert r.attempts == 1
    chat = next(s for s in finished_spans(sdk) if s.name == "chat mock/fast")
    a = chat.attributes
    assert a[attrs.GEN_AI_USAGE_INPUT_TOKENS] == 10
    assert a[attrs.GEN_AI_USAGE_OUTPUT_TOKENS] == 5
    assert a[attrs.ATTEMPT] == 1
    assert a[attrs.STAGE] == "extract"
    assert a[attrs.OUTPUT_PARSE_OK] is True
    assert a[attrs.OUTPUT_CONTROL_CHARS] == 0
    # pricing: 10*1 + 5*2 per 1M
    assert a[attrs.COST_USD] == pytest.approx(0.00002)


def test_retries_on_429_then_succeeds(sdk):
    calls = {"n": 0}

    def call(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise HttpError(429)
        return _ok()

    with ao.job("j"), ao.stage("s"):
        r = ao.llm_call(call, provider="mock", model="mock/fast", max_attempts=3)
    assert r.attempts == 2
    chats = [s for s in finished_spans(sdk) if s.name.startswith("chat")]
    assert len(chats) == 2
    failed = next(s for s in chats if s.attributes[attrs.ATTEMPT] == 1)
    assert failed.status.status_code.name == "ERROR"
    assert failed.attributes[attrs.RETRY_REASON] == attrs.REASON_RATE_LIMITED


def test_non_retryable_4xx_is_not_retried(sdk):
    calls = {"n": 0}

    def call(t):
        calls["n"] += 1
        raise HttpError(400)

    with ao.job("j"), ao.stage("s"), pytest.raises(ao.NonRetryableError):
        ao.llm_call(call, provider="mock", model="mock/fast", max_attempts=3)
    assert calls["n"] == 1


def test_parse_failure_is_retried_and_counted(sdk):
    calls = {"n": 0}

    def call(t):
        calls["n"] += 1
        return _ok("not json" if calls["n"] == 1 else '{"ok": true}')

    with ao.job("j"), ao.stage("s"):
        r = ao.llm_call(call, provider="mock", model="mock/fast", parse=ao.parsers.json_object)
    assert r.output == {"ok": True}
    chats = [s for s in finished_spans(sdk) if s.name.startswith("chat")]
    bad = next(s for s in chats if s.attributes[attrs.ATTEMPT] == 1)
    assert bad.attributes[attrs.OUTPUT_PARSE_OK] is False
    assert bad.attributes[attrs.RETRY_REASON] == attrs.REASON_PARSE_ERROR


def test_deadline_bounds_total_time(sdk, monkeypatch):
    """整体耗时不超过 deadline_s + 1s；每次尝试的 timeout = min(attempt_timeout, 剩余 deadline)。"""
    monkeypatch.setattr(llm, "backoff_s", lambda attempt, ra, remaining: min(0.05, remaining))
    timeouts: list[float] = []

    def call(t):
        timeouts.append(t)
        time.sleep(min(t, 0.2))
        raise TimeoutError("slow")

    t0 = time.monotonic()
    with ao.job("j"), ao.stage("s"), pytest.raises(ao.LLMCallError) as ei:
        ao.llm_call(call, provider="mock", model="mock/fast", attempt_timeout_s=0.2, deadline_s=0.5, max_attempts=10)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5 + 1.0
    assert ei.value.reason == attrs.REASON_TIMEOUT
    assert all(t <= 0.2 + 1e-6 for t in timeouts)
    assert timeouts[-1] < 0.2  # 最后一次用的是剩余 deadline


def test_provider_cost_takes_precedence(sdk):
    resp = _ok()
    resp["usage"]["cost"] = 0.5
    with ao.job("j"), ao.stage("s"):
        r = ao.llm_call(lambda t: resp, provider="mock", model="mock/fast")
    assert r.cost_usd == 0.5


def test_unknown_model_has_no_cost(sdk):
    with ao.job("j"), ao.stage("s"):
        r = ao.llm_call(lambda t: _ok(model="unknown/x"), provider="mock", model="unknown/x")
    assert r.cost_usd is None
    chat = next(s for s in finished_spans(sdk) if s.name.startswith("chat"))
    assert attrs.COST_USD not in chat.attributes


def test_control_chars_counted(sdk):
    with ao.job("j"), ao.stage("s"):
        ao.llm_call(lambda t: _ok("ab\x00c\x1fd"), provider="mock", model="mock/fast")
    chat = next(s for s in finished_spans(sdk) if s.name.startswith("chat"))
    assert chat.attributes[attrs.OUTPUT_CONTROL_CHARS] == 2


async def test_async_variant(sdk):
    async def call(t):
        return _ok()

    with ao.job("j"), ao.stage("s"):
        r = await ao.llm_call_async(call, provider="mock", model="mock/fast")
    assert r.attempts == 1


def test_openai_client_retries_disabled_guard():
    """文档性测试：下游必须 max_retries=0。这里检查 openai SDK 支持该参数。"""
    openai = pytest.importorskip("openai")
    c = openai.OpenAI(api_key="x", base_url="http://localhost:1/v1", max_retries=0)
    assert c.max_retries == 0
