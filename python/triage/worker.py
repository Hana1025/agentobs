"""分诊主循环（SPEC 6.1）。

read → 拉 trace → 规则 → 状态 → S1 → 路由 → (S2) → POST → archive。
trace 不存在 / 没有根 span / 处理异常：不归档，等可见性超时重读；read_ct 达到 max_reads 进 DLQ。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from typing import Any

from prometheus_client import start_http_server

from agentobs.queue.port import Message, QueuePort, dead_letter, queue_from_url
from triage import metrics as m
from triage.backends import make_decider
from triage.client import ServerClient
from triage.config import TriageConfig
from triage.models import Answer, Decider, TraceState, TriageResult, default_questions
from triage.router import HourlyBudget, needs_review, route, severity_of
from triage.rules import evaluate
from triage.state import extract_state

log = logging.getLogger("triage")


class Outcome:
    ARCHIVE = "archive"
    DEFER = "defer"
    DLQ = "dlq"


class TriageWorker:
    def __init__(
        self,
        cfg: TriageConfig,
        queue: QueuePort,
        server: ServerClient,
        decider: Decider,
        s2: Any | None = None,  # triage.s2.SystemTwo；None = 不做 S2
    ):
        self.cfg = cfg
        self.queue = queue
        self.server = server
        self.decider = decider
        self.s2 = s2
        self.version = cfg.version(decider.name, decider.version)
        self.budget = HourlyBudget(cfg.routing.s2_budget_per_hour)
        self._baselines: dict[str, tuple[float, dict[str, Any]]] = {}
        self._stop = False

    # ---------- 单条 ----------

    def baselines(self, project_id: str) -> dict[str, Any] | None:
        now = time.monotonic()
        cached = self._baselines.get(project_id)
        if cached and now - cached[0] < 60:
            return cached[1]
        try:
            b = self.server.get_baselines(project_id, self.cfg.thresholds.baseline_window)
        except Exception:  # noqa: BLE001
            log.warning("baselines unavailable for %s", project_id, exc_info=True)
            return cached[1] if cached else None
        self._baselines[project_id] = (now, b)
        return b

    def triage_trace(self, trace: dict[str, Any]) -> TriageResult:
        """对已拉到的 trace JSON 做完整分诊（不含 I/O 之外的副作用；S2 调用除外）。"""
        project_id = trace["summary"].get("projectId") or "default"
        state = extract_state(
            trace, self.baselines(project_id), self.cfg.thresholds.baseline_min_samples
        )
        fired = evaluate(state, self.cfg)
        state.rules_fired = [f.id for f in fired]

        t0 = time.monotonic()
        questions = default_questions(state.stage_names())
        answers_list = self.decider.decide(state, questions)
        answers: dict[str, Answer] = {a.question_id: a for a in answers_list}
        m.triage_latency_seconds.labels(system="s1").observe(time.monotonic() - t0)
        s1_cost = float(getattr(self.decider, "last_cost_usd", 0.0) or 0.0)
        if s1_cost:
            m.triage_cost_usd_total.labels(system="s1").inc(s1_cost)

        health = answers["health"]
        r = route(answers, fired, self.cfg.routing)
        severity = severity_of(answers)

        result = TriageResult(
            trace_id=state.trace_id,
            project_id=project_id,
            job_id=state.job_id,
            triage_version=self.version,
            rules_fired=state.rules_fired,
            state=state.model_dump(exclude_none=True),
            s1_backend=self.decider.name,
            s1_answers={a.question_id: a.model_dump(exclude={"question_id"}) for a in answers_list},
            s1_label=health.answer,
            s1_confidence=health.confidence,
            route=r,
            triage_cost_usd=s1_cost,
        )

        if r == "escalate":
            if self.s2 is None or not self.cfg.s2.enabled:
                result.review_status = (
                    "pending" if needs_review(None, severity, self.cfg.review) else "none"
                )
            elif not self.budget.try_acquire():
                log.info("s2 budget exhausted; downgrading %s to alert", state.trace_id)
                result.route = "alert"
            else:
                self._run_s2(result, state, trace, severity)
        if (
            result.route == "alert"
            and result.review_status == "none"
            and severity >= self.cfg.review.or_severity_gte
        ):
            result.review_status = "pending"

        m.triage_total.labels(
            route=result.route, s1_label=result.s1_label, backend=self.decider.name
        ).inc()
        m.triage_confidence.observe(result.s1_confidence)
        return result

    def _run_s2(
        self, result: TriageResult, state: TraceState, trace: dict[str, Any], severity: int
    ) -> None:
        t0 = time.monotonic()
        s2_failed = False
        try:
            s2 = self.s2.analyze(state, trace["spans"], include_content=self.cfg.s2.include_content)
            result.s2_model = s2.model
            result.s2_root_cause = s2.root_cause
            result.s2_confidence = s2.confidence
            result.s2_evidence = [e.model_dump() for e in s2.evidence]
            result.s2_summary = s2.summary
            result.triage_cost_usd += s2.cost_usd
            if s2.cost_usd:
                m.triage_cost_usd_total.labels(system="s2").inc(s2.cost_usd)
            s2_failed = s2.root_cause == "other" and s2.confidence <= 0.0
        except Exception:  # noqa: BLE001
            log.error("system 2 failed for %s", state.trace_id, exc_info=True)
            result.s2_root_cause = "other"
            result.s2_confidence = 0.0
            result.s2_summary = "system 2 failed"
            s2_failed = True
        finally:
            m.triage_latency_seconds.labels(system="s2").observe(time.monotonic() - t0)
        if needs_review(result.s2_confidence, severity, self.cfg.review, s2_failed=s2_failed):
            result.review_status = "pending"

    def handle(self, msg: Message) -> str:
        body = msg.message
        trace_id = body.get("trace_id")
        if not trace_id:
            return self._dead_letter(msg, "missing trace_id")
        try:
            trace = self.server.get_trace(trace_id)
        except Exception:  # noqa: BLE001
            log.warning("server unavailable while fetching %s", trace_id, exc_info=True)
            return self._defer(msg, "server_unavailable")
        if trace is None or not trace["summary"].get("hasRoot"):
            # 时序陷阱：span 可能还没入库
            return self._defer(msg, "trace_not_ready")
        try:
            result = self.triage_trace(trace)
            status = self.server.post_triage(result.model_dump(exclude_none=True))
        except Exception:  # noqa: BLE001
            log.error("triage failed for %s", trace_id, exc_info=True)
            return self._defer(msg, "triage_error")
        if status == "exists":
            log.info(
                "triage for %s@%s already exists; archiving duplicate delivery",
                trace_id,
                self.version,
            )
        self.queue.archive(self.cfg.worker.queue, msg.msg_id)
        return Outcome.ARCHIVE

    def _defer(self, msg: Message, reason: str) -> str:
        if msg.read_ct >= self.cfg.worker.max_reads:
            return self._dead_letter(msg, reason)
        m.triage_deferred_total.labels(reason=reason).inc()
        return Outcome.DEFER

    def _dead_letter(self, msg: Message, reason: str) -> str:
        try:
            dead_letter(self.queue, self.cfg.worker.queue, self.cfg.worker.dlq, msg, reason)
            m.triage_dlq_total.labels(reason=reason).inc()
            log.error("message %s moved to DLQ: %s", msg.msg_id, reason)
        except Exception:  # noqa: BLE001
            log.error("failed to dead-letter message %s", msg.msg_id, exc_info=True)
            return Outcome.DEFER
        return Outcome.DLQ

    # ---------- 循环 ----------

    def run_once(self) -> int:
        w = self.cfg.worker
        msgs = self.queue.read(w.queue, w.visibility_timeout_s, w.batch)
        for msg in msgs:
            if self._stop:
                break
            self.handle(msg)
        return len(msgs)

    def run_forever(self) -> None:
        log.info("triage worker started: backend=%s version=%s", self.decider.name, self.version)
        while not self._stop:
            try:
                n = self.run_once()
            except Exception:  # noqa: BLE001
                log.error("worker loop error", exc_info=True)
                n = 0
            if n == 0:
                time.sleep(self.cfg.worker.idle_sleep_s)

    def stop(self, *_: Any) -> None:
        self._stop = True


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = TriageConfig.load()
    # postgresql://… → pgmq，amqp://… → RabbitMQ
    queue = queue_from_url(os.environ.get("AGENTOBS_QUEUE_URL") or os.environ["AGENTOBS_QUEUE_DSN"])
    server = ServerClient(
        os.environ.get("AGENTOBS_SERVER_URL", "http://localhost:8080"),
        os.environ.get("AGENTOBS_INTERNAL_TOKEN", ""),
    )
    from triage.pricing import load_triage_pricing

    pricing = load_triage_pricing(cfg.pricing)
    decider = make_decider(cfg, pricing=pricing)
    s2 = None
    if cfg.s2.enabled:
        from triage.s2 import make_system_two

        s2 = make_system_two(pricing=pricing)
    port = int(os.environ.get("TRIAGE_METRICS_PORT", "9465"))
    if port:
        start_http_server(port)
    worker = TriageWorker(cfg, queue, server, decider, s2)
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
