"""llm_call：一次模型调用的封装——每次尝试一个 span、单层重试、deadline、指标、成本。"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from agentobs.core import attrs, metrics
from agentobs.core.parsers import ParseError
from agentobs.core.tracer import current_job, current_stage, state

log = logging.getLogger("agentobs")

_CONTROL = {chr(c) for c in range(0x00, 0x20)} - {"\t", "\n", "\r"}
_CONTROL.add("\x7f")


class LLMCallError(RuntimeError):
    """所有尝试都失败后抛出；``reason`` 是最后一次失败的分类，``cause`` 是原始异常。"""

    def __init__(self, reason: str, cause: BaseException | None, attempts: int):
        super().__init__(f"llm_call failed after {attempts} attempt(s): {reason}: {cause}")
        self.reason = reason
        self.cause = cause
        self.attempts = attempts


class NonRetryableError(RuntimeError):
    def __init__(self, cause: BaseException):
        super().__init__(f"non-retryable: {cause}")
        self.cause = cause


@dataclass
class LLMResult:
    output: Any  # parse 后的结果；没有 parse 时是文本
    text: str | None
    raw: Any
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    attempts: int
    finish_reason: str | None = None


# ---------- 响应解析（支持 openai SDK 对象、dict、纯字符串） ----------


def _get(obj: Any, *path: str) -> Any:
    cur = obj
    for p in path:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            cur = getattr(cur, p, None)
    return cur


def extract_response(resp: Any) -> dict[str, Any]:
    """从响应里取文本、usage、模型、finish_reason、供应商报的费用。"""
    if isinstance(resp, str):
        return {"text": resp}
    choices = _get(resp, "choices")
    text = None
    finish = None
    if choices:
        c0 = choices[0]
        text = _get(c0, "message", "content") or _get(c0, "text")
        finish = _get(c0, "finish_reason")
    if text is None:
        text = _get(resp, "output_text") or _get(resp, "text")
    usage = _get(resp, "usage")
    return {
        "text": text,
        "model": _get(resp, "model"),
        "input_tokens": _as_int(_get(usage, "prompt_tokens") or _get(usage, "input_tokens")),
        "output_tokens": _as_int(_get(usage, "completion_tokens") or _get(usage, "output_tokens")),
        "cost": _as_float(_get(usage, "cost")),
        "finish_reason": finish,
    }


def _as_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------- 错误分类 ----------


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def _retry_after(exc: BaseException) -> float | None:
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        ra = headers.get("retry-after") or headers.get("Retry-After")
        return float(ra) if ra else None
    except (TypeError, ValueError):
        return None


def classify(exc: BaseException) -> str | None:
    """返回重试原因；None 表示不可重试。"""
    if isinstance(exc, ParseError):
        return attrs.REASON_PARSE_ERROR
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return attrs.REASON_TIMEOUT
    name = type(exc).__name__
    mod = type(exc).__module__ or ""
    if "Timeout" in name:
        return attrs.REASON_TIMEOUT
    code = _status_code(exc)
    if code == 429:
        return attrs.REASON_RATE_LIMITED
    if code is not None and 500 <= code < 600:
        return attrs.REASON_SERVER_ERROR
    if code is not None and 400 <= code < 500:
        return None
    if "Connect" in name or "Connection" in name or isinstance(exc, ConnectionError | OSError):
        return attrs.REASON_CONNECTION
    if mod.startswith("openai") and name in {"InternalServerError", "APIConnectionError"}:
        return attrs.REASON_SERVER_ERROR if "Server" in name else attrs.REASON_CONNECTION
    return None


def backoff_s(attempt: int, retry_after: float | None, remaining: float) -> float:
    """2s 起翻倍 + 随机抖动；有 Retry-After 按它；不超过剩余 deadline。"""
    if retry_after is not None:
        delay = retry_after
    else:
        base = 2.0 * (2 ** (attempt - 1))
        delay = base + random.uniform(0, base * 0.25)
    return max(0.0, min(delay, remaining))


# ---------- span 填充 ----------


def _start_attempt_span(provider: str, model: str, attempt: int) -> Span | None:
    try:
        a: dict[str, Any] = {
            attrs.GEN_AI_OPERATION_NAME: "chat",
            attrs.GEN_AI_PROVIDER_NAME: provider,
            attrs.GEN_AI_REQUEST_MODEL: model,
            attrs.ATTEMPT: attempt,
        }
        j = current_job()
        st = current_stage()
        if j is not None:
            a[attrs.JOB_ID] = j.job_id
        if st is not None:
            a[attrs.STAGE] = st.name
        return state.tracer.start_span(f"chat {model}", kind=SpanKind.CLIENT, attributes=a)
    except Exception:  # noqa: BLE001
        log.warning("agentobs.llm_call: failed to start span", exc_info=True)
        return None


def _fill_success(
    span: Span | None,
    info: dict[str, Any],
    text: str | None,
    parse_ok: bool | None,
    cost: float | None,
    input_content: str | None,
) -> None:
    if span is None:
        return
    try:
        if info.get("model"):
            span.set_attribute(attrs.GEN_AI_RESPONSE_MODEL, str(info["model"]))
        if info.get("input_tokens") is not None:
            span.set_attribute(attrs.GEN_AI_USAGE_INPUT_TOKENS, info["input_tokens"])
        if info.get("output_tokens") is not None:
            span.set_attribute(attrs.GEN_AI_USAGE_OUTPUT_TOKENS, info["output_tokens"])
        if info.get("finish_reason"):
            span.set_attribute(attrs.GEN_AI_RESPONSE_FINISH_REASONS, [str(info["finish_reason"])])
        if cost is not None:
            span.set_attribute(attrs.COST_USD, cost)
        if text is not None:
            span.set_attribute(attrs.OUTPUT_CHARS, len(text))
            span.set_attribute(attrs.OUTPUT_CONTROL_CHARS, sum(1 for ch in text if ch in _CONTROL))
        if parse_ok is not None:
            span.set_attribute(attrs.OUTPUT_PARSE_OK, parse_ok)
        s = state.settings
        if s.capture_content:
            if input_content is not None:
                span.set_attribute(
                    attrs.CONTENT_INPUT, s.redactor.redact(input_content)[: s.content_max_chars]
                )
            if text is not None:
                span.set_attribute(
                    attrs.CONTENT_OUTPUT, s.redactor.redact(text)[: s.content_max_chars]
                )
    except Exception:  # noqa: BLE001
        log.warning("agentobs.llm_call: failed to set attributes", exc_info=True)


def _record_metrics(
    stage: str, model: str, status: str, latency: float, info: dict[str, Any], cost: float | None
) -> None:
    try:
        metrics.llm_requests_total.labels(stage=stage, model=model, status=status).inc()
        metrics.llm_latency_seconds.labels(stage=stage, model=model).observe(latency)
        if info.get("input_tokens"):
            metrics.llm_tokens_total.labels(stage=stage, model=model, direction="input").inc(
                info["input_tokens"]
            )
        if info.get("output_tokens"):
            metrics.llm_tokens_total.labels(stage=stage, model=model, direction="output").inc(
                info["output_tokens"]
            )
        if cost:
            metrics.llm_cost_usd_total.labels(stage=stage, model=model).inc(cost)
    except Exception:  # noqa: BLE001
        pass


def _compute_cost(model: str, info: dict[str, Any]) -> float | None:
    """优先供应商返回的费用，没有按 pricing 表算，都没有则 None（不猜）。"""
    if info.get("cost") is not None:
        return round(float(info["cost"]), 6)
    return state.settings.pricing.cost(
        info.get("model") or model, info.get("input_tokens"), info.get("output_tokens")
    )


# ---------- 主逻辑 ----------


class _Attempt:
    """一次尝试的共享逻辑（同步和异步版本都用）。"""

    def __init__(
        self,
        provider: str,
        model: str,
        parse: Callable[[str], Any] | None,
        input_content: str | None,
    ):
        self.provider = provider
        self.model = model
        self.parse = parse
        self.input_content = input_content
        self.stage = current_stage().name if current_stage() else "none"

    def begin(self, attempt: int) -> tuple[Span | None, float]:
        return _start_attempt_span(self.provider, self.model, attempt), time.monotonic()

    def success(self, span: Span | None, t0: float, resp: Any, attempt: int) -> LLMResult:
        info = extract_response(resp)
        text = info.get("text")
        cost = _compute_cost(self.model, info)
        parse_ok: bool | None = None
        output: Any = text
        parse_exc: ParseError | None = None
        if self.parse is not None:
            try:
                output = self.parse(text if text is not None else "")
                parse_ok = True
            except ParseError as e:
                parse_ok = False
                parse_exc = e
            except Exception as e:  # noqa: BLE001
                parse_ok = False
                parse_exc = ParseError(str(e))
        latency = time.monotonic() - t0
        _fill_success(span, info, text, parse_ok, cost, self.input_content)
        if parse_exc is not None:
            try:
                metrics.output_parse_failures_total.labels(stage=self.stage).inc()
            except Exception:  # noqa: BLE001
                pass
            _record_metrics(self.stage, self.model, "error", latency, info, cost)
            self._end_error(span, parse_exc, attrs.REASON_PARSE_ERROR)
            raise parse_exc
        _record_metrics(self.stage, self.model, "ok", latency, info, cost)
        try:
            if span is not None:
                span.set_status(Status(StatusCode.OK))
                span.end()
        except Exception:  # noqa: BLE001
            pass
        return LLMResult(
            output=output,
            text=text,
            raw=resp,
            model=info.get("model") or self.model,
            input_tokens=info.get("input_tokens"),
            output_tokens=info.get("output_tokens"),
            cost_usd=cost,
            attempts=attempt,
            finish_reason=info.get("finish_reason"),
        )

    def failure(self, span: Span | None, t0: float, exc: BaseException) -> str | None:
        reason = classify(exc)
        latency = time.monotonic() - t0
        _record_metrics(self.stage, self.model, "error", latency, {}, None)
        self._end_error(span, exc, reason)
        return reason

    def _end_error(self, span: Span | None, exc: BaseException, reason: str | None) -> None:
        try:
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"[:500]))
                if reason:
                    span.set_attribute(attrs.RETRY_REASON, reason)
                span.record_exception(exc)
                span.end()
        except Exception:  # noqa: BLE001
            pass

    def count_retry(self, reason: str) -> None:
        try:
            metrics.llm_retries_total.labels(
                stage=self.stage, model=self.model, reason=reason
            ).inc()
        except Exception:  # noqa: BLE001
            pass


def llm_call(
    call: Callable[[float], Any],
    *,
    provider: str,
    model: str,
    attempt_timeout_s: float = 60,
    deadline_s: float = 300,
    max_attempts: int = 3,
    parse: Callable[[str], Any] | None = None,
    input_content: str | None = None,
) -> LLMResult:
    """同步版本。``call(timeout)`` 必须把 timeout 传给底层客户端，且底层客户端重试已关闭。

    重试：超时、429、5xx、连接错误、解析失败；其他 4xx 直接抛 NonRetryableError。
    每次尝试超时 = min(attempt_timeout_s, 剩余 deadline)；超过 deadline 不再重试。
    """
    a = _Attempt(provider, model, parse, input_content)
    start = time.monotonic()
    last: BaseException | None = None
    last_reason: str | None = None
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        remaining = deadline_s - (time.monotonic() - start)
        if remaining <= 0:
            break
        timeout = min(attempt_timeout_s, remaining)
        span, t0 = a.begin(attempt)
        try:
            resp = call(timeout)
            return a.success(span, t0, resp, attempt)
        except ParseError as e:
            # success() 已记录 span/指标
            last, last_reason = e, attrs.REASON_PARSE_ERROR
        except Exception as e:  # noqa: BLE001
            reason = a.failure(span, t0, e)
            if reason is None:
                raise NonRetryableError(e) from e
            last, last_reason = e, reason
        if attempt >= max_attempts:
            break
        remaining = deadline_s - (time.monotonic() - start)
        if remaining <= 0:
            break
        a.count_retry(last_reason or "unknown")
        time.sleep(backoff_s(attempt, _retry_after(last) if last else None, remaining))
    raise LLMCallError(last_reason or "deadline", last, attempt)


async def llm_call_async(
    call: Callable[[float], Awaitable[Any]],
    *,
    provider: str,
    model: str,
    attempt_timeout_s: float = 60,
    deadline_s: float = 300,
    max_attempts: int = 3,
    parse: Callable[[str], Any] | None = None,
    input_content: str | None = None,
) -> LLMResult:
    a = _Attempt(provider, model, parse, input_content)
    start = time.monotonic()
    last: BaseException | None = None
    last_reason: str | None = None
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        remaining = deadline_s - (time.monotonic() - start)
        if remaining <= 0:
            break
        timeout = min(attempt_timeout_s, remaining)
        span, t0 = a.begin(attempt)
        try:
            resp = call(timeout)
            if inspect.isawaitable(resp):
                resp = await asyncio.wait_for(resp, timeout=timeout + 1)
            return a.success(span, t0, resp, attempt)
        except ParseError as e:
            last, last_reason = e, attrs.REASON_PARSE_ERROR
        except Exception as e:  # noqa: BLE001
            reason = a.failure(span, t0, e)
            if reason is None:
                raise NonRetryableError(e) from e
            last, last_reason = e, reason
        if attempt >= max_attempts:
            break
        remaining = deadline_s - (time.monotonic() - start)
        if remaining <= 0:
            break
        a.count_retry(last_reason or "unknown")
        await asyncio.sleep(backoff_s(attempt, _retry_after(last) if last else None, remaining))
    raise LLMCallError(last_reason or "deadline", last, attempt)


def current_span_id() -> str | None:
    try:
        return format(trace.get_current_span().get_span_context().span_id, "016x")
    except Exception:  # noqa: BLE001
        return None
