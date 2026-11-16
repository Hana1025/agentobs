"""Prometheus 指标（SPEC 5.3）。标签里绝不放 job_id / trace_id 等高基数值。"""

from __future__ import annotations

import logging
import threading
import time

from prometheus_client import Counter, Gauge, Histogram, start_http_server

log = logging.getLogger("agentobs")

LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300)
JOB_BUCKETS = (1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600)

llm_requests_total = Counter(
    "agentobs_llm_requests_total", "LLM call attempts", ["stage", "model", "status"]
)
llm_latency_seconds = Histogram(
    "agentobs_llm_latency_seconds", "LLM attempt latency", ["stage", "model"], buckets=LATENCY_BUCKETS
)
llm_tokens_total = Counter("agentobs_llm_tokens_total", "LLM tokens", ["stage", "model", "direction"])
llm_cost_usd_total = Counter("agentobs_llm_cost_usd_total", "LLM cost in USD", ["stage", "model"])
llm_retries_total = Counter("agentobs_llm_retries_total", "LLM retries", ["stage", "model", "reason"])
output_parse_failures_total = Counter(
    "agentobs_output_parse_failures_total", "LLM output parse failures", ["stage"]
)
jobs_total = Counter("agentobs_jobs_total", "Finished jobs", ["status"])
job_duration_seconds = Histogram(
    "agentobs_job_duration_seconds", "Job duration", ["status"], buckets=JOB_BUCKETS
)
jobs_in_progress = Gauge("agentobs_jobs_in_progress", "Jobs currently running")
job_oldest_in_progress_seconds = Gauge(
    "agentobs_job_oldest_in_progress_seconds", "Age of the oldest running job"
)
queue_send_failures_total = Counter(
    "agentobs_queue_send_failures_total", "Failed queue sends", ["queue"]
)


class _InProgress:
    """跟踪进行中的 job 起始时间，供 oldest gauge 用。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._starts: dict[int, float] = {}
        self._seq = 0
        job_oldest_in_progress_seconds.set_function(self.oldest_age)

    def start(self) -> int:
        with self._lock:
            self._seq += 1
            self._starts[self._seq] = time.monotonic()
            jobs_in_progress.set(len(self._starts))
            return self._seq

    def finish(self, token: int) -> None:
        with self._lock:
            self._starts.pop(token, None)
            jobs_in_progress.set(len(self._starts))

    def oldest_age(self) -> float:
        with self._lock:
            if not self._starts:
                return 0.0
            return time.monotonic() - min(self._starts.values())


in_progress = _InProgress()

_server_started = False


def start_metrics_server(port: int | None) -> None:
    global _server_started
    if port is None or _server_started:
        return
    try:
        start_http_server(port)
        _server_started = True
    except Exception:  # noqa: BLE001
        log.warning("agentobs: failed to start metrics server on %s", port, exc_info=True)
