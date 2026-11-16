"""init / job / stage：下游接入 OpenTelemetry 的唯一入口。

所有埋点异常只记日志，不抛给调用方；业务异常原样重新抛出。
"""

from __future__ import annotations

import contextvars
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from agentobs.core import attrs, metrics
from agentobs.core.pricing import PricingTable
from agentobs.core.redact import DefaultRedactor, Redactor
from agentobs.queue.port import JOB_FINISHED, QueuePort

log = logging.getLogger("agentobs")

MESSAGE_VERSION = 1


@dataclass
class Settings:
    service_name: str = "unknown-service"
    project_id: str = "default"
    otlp_endpoint: str | None = None
    metrics_port: int | None = None
    capture_content: bool = False
    redactor: Redactor = field(default_factory=DefaultRedactor)
    queue: QueuePort | None = None
    pricing: PricingTable = field(default_factory=PricingTable)
    content_max_chars: int = attrs.CONTENT_MAX_CHARS


class _State:
    settings: Settings = Settings()
    provider: TracerProvider | None = None
    tracer: trace.Tracer = trace.get_tracer("agentobs")
    initialized: bool = False


state = _State()

_current_job: contextvars.ContextVar[Job | None] = contextvars.ContextVar(
    "agentobs_job", default=None
)
_current_stage: contextvars.ContextVar[Stage | None] = contextvars.ContextVar(
    "agentobs_stage", default=None
)


def init(
    service_name: str,
    project_id: str,
    otlp_endpoint: str | None = None,
    metrics_port: int | None = None,
    capture_content: bool = False,
    redactor: Redactor | None = None,
    queue: QueuePort | None = None,
    pricing: str | dict | PricingTable | None = None,
    *,
    exporter: SpanExporter | None = None,
    export_timeout_s: int = 10,
) -> None:
    """初始化 SDK。重复调用会用新设置替换旧的（主要用于测试）。

    ``exporter`` 可传入自定义 SpanExporter（测试用 InMemorySpanExporter）；否则按 ``otlp_endpoint`` 建 OTLP/HTTP 导出器。
    """
    try:
        s = Settings(
            service_name=service_name,
            project_id=project_id,
            otlp_endpoint=otlp_endpoint,
            metrics_port=metrics_port,
            capture_content=capture_content,
            redactor=redactor or DefaultRedactor(),
            queue=queue,
            pricing=pricing if isinstance(pricing, PricingTable) else PricingTable.load(pricing),
        )
        resource = Resource.create({attrs.SERVICE_NAME: service_name, attrs.PROJECT_ID: project_id})
        provider = TracerProvider(resource=resource)
        if exporter is None and otlp_endpoint:
            exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint.rstrip("/") + "/v1/traces", timeout=export_timeout_s
            )
        if exporter is not None:
            provider.add_span_processor(
                BatchSpanProcessor(exporter, schedule_delay_millis=1000, max_export_batch_size=256)
            )
        if state.provider is not None:
            try:
                state.provider.shutdown()
            except Exception:  # noqa: BLE001
                pass
        state.provider = provider
        state.tracer = provider.get_tracer("agentobs", "0.1.0")
        state.settings = s
        state.initialized = True
        metrics.start_metrics_server(metrics_port)
    except Exception:  # noqa: BLE001
        log.error("agentobs.init failed; SDK will run in no-op mode", exc_info=True)


def shutdown() -> None:
    """flush 并关闭导出器（进程退出前调用）。"""
    try:
        if state.provider is not None:
            state.provider.force_flush(timeout_millis=10_000)
            state.provider.shutdown()
    except Exception:  # noqa: BLE001
        log.warning("agentobs.shutdown failed", exc_info=True)


def flush(timeout_s: float = 10) -> None:
    try:
        if state.provider is not None:
            state.provider.force_flush(timeout_millis=int(timeout_s * 1000))
    except Exception:  # noqa: BLE001
        log.warning("agentobs.flush failed", exc_info=True)


def _set_metric(span: Span | None, name: str, value: float | int) -> None:
    try:
        if span is not None and isinstance(value, int | float) and not isinstance(value, bool):
            span.set_attribute(attrs.METRIC_PREFIX + name, value)
    except Exception:  # noqa: BLE001
        log.warning("agentobs.set_metric failed", exc_info=True)


class Stage:
    def __init__(self, name: str, span: Span | None):
        self.name = name
        self.span = span

    def set_metric(self, name: str, value: float | int) -> None:
        _set_metric(self.span, name, value)

    def set_attribute(self, key: str, value: Any) -> None:
        try:
            if self.span is not None:
                self.span.set_attribute(key, value)
        except Exception:  # noqa: BLE001
            log.warning("agentobs.set_attribute failed", exc_info=True)


class Job:
    def __init__(self, job_id: str, span: Span | None):
        self.job_id = job_id
        self.span = span
        self.trace_id: str = ""
        self.started = time.monotonic()
        self._event_sent = False
        self._progress_token: int | None = None
        try:
            if span is not None:
                self.trace_id = format(span.get_span_context().trace_id, "032x")
        except Exception:  # noqa: BLE001
            pass

    def set_metric(self, name: str, value: float | int) -> None:
        _set_metric(self.span, name, value)

    def set_attribute(self, key: str, value: Any) -> None:
        try:
            if self.span is not None:
                self.span.set_attribute(key, value)
        except Exception:  # noqa: BLE001
            log.warning("agentobs.set_attribute failed", exc_info=True)

    def message(self, status: str) -> dict[str, Any]:
        return {
            "v": MESSAGE_VERSION,
            "project_id": state.settings.project_id,
            "job_id": self.job_id,
            "trace_id": self.trace_id,
            "status": status,
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        }

    def finish_in(self, conn: Any, status: str = "succeeded") -> int:
        """事务模式：在调用方的连接/事务里发 job_finished；异常抛给调用方。"""
        q = state.settings.queue
        if q is None:
            raise RuntimeError("agentobs: no queue configured (init(queue=...))")
        msg_id = q.send(JOB_FINISHED, self.message(status), conn=conn)
        self._event_sent = True
        return msg_id

    def _send_default(self, status: str) -> None:
        if self._event_sent:
            return
        q = state.settings.queue
        if q is None:
            return
        try:
            q.send(JOB_FINISHED, self.message(status))
            self._event_sent = True
        except Exception:  # noqa: BLE001
            metrics.queue_send_failures_total.labels(queue=JOB_FINISHED).inc()
            log.error("agentobs: failed to send job_finished for %s", self.job_id, exc_info=True)


def current_job() -> Job | None:
    return _current_job.get()


def current_stage() -> Stage | None:
    return _current_stage.get()


def current_stage_name() -> str:
    st = _current_stage.get()
    return st.name if st else "none"


@contextmanager
def job(job_id: str, attributes: dict[str, Any] | None = None) -> Iterator[Job]:
    """根 span。退出时：设状态（异常 = ERROR 并重新抛出），然后发 job_finished。"""
    span: Span | None = None
    try:
        span = state.tracer.start_span(
            f"job {job_id}", kind=SpanKind.INTERNAL, attributes={attrs.JOB_ID: job_id}
        )
        for k, v in (attributes or {}).items():
            if isinstance(v, int | float) and not isinstance(v, bool):
                span.set_attribute(attrs.METRIC_PREFIX + k, v)
            else:
                span.set_attribute(f"agentobs.job.{k}", str(v))
    except Exception:  # noqa: BLE001
        log.error("agentobs.job: failed to start span", exc_info=True)
    j = Job(job_id, span)
    try:
        j._progress_token = metrics.in_progress.start()
    except Exception:  # noqa: BLE001
        pass
    token_job = _current_job.set(j)
    ctx_token = None
    try:
        if span is not None:
            ctx_token = trace.context_api.attach(trace.set_span_in_context(span))
    except Exception:  # noqa: BLE001
        pass
    status = "succeeded"
    try:
        yield j
    except BaseException as e:
        status = "failed"
        try:
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, f"{type(e).__name__}: {e}"[:500]))
                span.record_exception(e)
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        try:
            if span is not None:
                if status == "succeeded":
                    span.set_status(Status(StatusCode.OK))
                span.end()
            if ctx_token is not None:
                trace.context_api.detach(ctx_token)
        except Exception:  # noqa: BLE001
            log.warning("agentobs.job: failed to end span", exc_info=True)
        _current_job.reset(token_job)
        try:
            dur = time.monotonic() - j.started
            metrics.jobs_total.labels(status=status).inc()
            metrics.job_duration_seconds.labels(status=status).observe(dur)
            if j._progress_token is not None:
                metrics.in_progress.finish(j._progress_token)
        except Exception:  # noqa: BLE001
            pass
        j._send_default(status)


@contextmanager
def stage(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Stage]:
    span: Span | None = None
    try:
        j = _current_job.get()
        a: dict[str, Any] = {attrs.STAGE: name}
        if j is not None:
            a[attrs.JOB_ID] = j.job_id
        span = state.tracer.start_span(f"stage {name}", kind=SpanKind.INTERNAL, attributes=a)
        for k, v in (attributes or {}).items():
            span.set_attribute(k, v)
    except Exception:  # noqa: BLE001
        log.error("agentobs.stage: failed to start span", exc_info=True)
    st = Stage(name, span)
    token = _current_stage.set(st)
    ctx_token = None
    try:
        if span is not None:
            ctx_token = trace.context_api.attach(trace.set_span_in_context(span))
    except Exception:  # noqa: BLE001
        pass
    try:
        yield st
    except BaseException as e:
        try:
            if span is not None:
                span.set_status(Status(StatusCode.ERROR, f"{type(e).__name__}: {e}"[:500]))
                span.record_exception(e)
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        try:
            if span is not None:
                span.set_status(Status(StatusCode.OK))
        except Exception:  # noqa: BLE001
            pass
    finally:
        try:
            if span is not None:
                span.end()
            if ctx_token is not None:
                trace.context_api.detach(ctx_token)
        except Exception:  # noqa: BLE001
            log.warning("agentobs.stage: failed to end span", exc_info=True)
        _current_stage.reset(token)
