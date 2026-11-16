"""构造 server 内部接口形状的 trace JSON（summary + spans）供分诊测试用。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from agentobs.core import attrs

T0 = datetime(2026, 11, 17, 1, 0, 0, tzinfo=UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class TraceBuilder:
    def __init__(self, trace_id: str = "a" * 32, project_id: str = "demo", job_id: str = "job-1"):
        self.trace_id = trace_id
        self.project_id = project_id
        self.job_id = job_id
        self.spans: list[dict[str, Any]] = []
        self._n = 0

    def _sid(self) -> str:
        self._n += 1
        return f"{self._n:016x}"

    def root(
        self,
        status: str = "OK",
        duration_s: float = 2.0,
        metrics: dict[str, float] | None = None,
        extra: dict | None = None,
    ) -> str:
        sid = self._sid()
        a = {attrs.JOB_ID: self.job_id}
        a.update({attrs.METRIC_PREFIX + k: v for k, v in (metrics or {}).items()})
        a.update(extra or {})
        self.spans.append(self._span(sid, None, f"job {self.job_id}", status, 0, duration_s, a))
        return sid

    def stage(
        self,
        name: str,
        parent: str,
        start: float,
        duration_s: float,
        status: str = "OK",
        metrics: dict[str, float] | None = None,
    ) -> str:
        sid = self._sid()
        a = {attrs.JOB_ID: self.job_id, attrs.STAGE: name}
        a.update({attrs.METRIC_PREFIX + k: v for k, v in (metrics or {}).items()})
        self.spans.append(
            self._span(sid, parent, f"stage {name}", status, start, duration_s, a, stage=name)
        )
        return sid

    def chat(
        self,
        stage: str,
        parent: str,
        start: float,
        duration_s: float,
        attempt: int = 1,
        status: str = "OK",
        reason: str | None = None,
        parse_ok: bool | None = True,
        control_chars: int = 0,
        cost: float = 0.001,
        model: str = "mock/fast",
    ) -> str:
        sid = self._sid()
        a: dict[str, Any] = {
            attrs.JOB_ID: self.job_id,
            attrs.STAGE: stage,
            attrs.GEN_AI_REQUEST_MODEL: model,
            attrs.ATTEMPT: attempt,
            attrs.OUTPUT_CHARS: 120,
            attrs.OUTPUT_CONTROL_CHARS: control_chars,
        }
        if reason:
            a[attrs.RETRY_REASON] = reason
        if parse_ok is not None:
            a[attrs.OUTPUT_PARSE_OK] = parse_ok
        if status == "OK":
            a[attrs.COST_USD] = cost
        self.spans.append(
            self._span(
                sid,
                parent,
                f"chat {model}",
                status,
                start,
                duration_s,
                a,
                stage=stage,
                attempt=attempt,
                cost=cost if status == "OK" else None,
                model=model,
            )
        )
        return sid

    def _span(
        self,
        sid,
        parent,
        name,
        status,
        start,
        dur,
        a,
        stage=None,
        attempt=None,
        cost=None,
        model=None,
    ) -> dict[str, Any]:
        s = T0 + timedelta(seconds=start)
        return {
            "spanId": sid,
            "parentSpanId": parent,
            "name": name,
            "kind": "INTERNAL",
            "startTime": _iso(s),
            "endTime": _iso(s + timedelta(seconds=dur)),
            "durationMs": dur * 1000,
            "statusCode": status,
            "stage": stage,
            "model": model,
            "attempt": attempt,
            "costUsd": cost,
            "attributes": a,
            "events": [],
            "depth": 0,
        }

    def build(self) -> dict[str, Any]:
        roots = [s for s in self.spans if not s["parentSpanId"]]
        starts = [s["startTime"] for s in self.spans]
        ends = [s["endTime"] for s in self.spans]
        costs = [s["costUsd"] for s in self.spans if s.get("costUsd") is not None]
        return {
            "summary": {
                "traceId": self.trace_id,
                "projectId": self.project_id,
                "jobId": self.job_id,
                "startTime": min(starts) if starts else _iso(T0),
                "endTime": max(ends) if ends else _iso(T0),
                "hasRoot": bool(roots),
                "rootStatus": roots[0]["statusCode"] if roots else None,
                "spanCount": len(self.spans),
                "errorCount": sum(1 for s in self.spans if s["statusCode"] == "ERROR"),
                "inputTokens": 100,
                "outputTokens": 50,
                "costUsd": round(sum(costs), 6) if costs else None,
                "maxAttempt": max((s["attempt"] or 0 for s in self.spans), default=None),
            },
            "spans": self.spans,
        }


def normal_trace(
    trace_id: str = "a" * 32, job_id: str = "job-1", score: float = 75.0
) -> dict[str, Any]:
    b = TraceBuilder(trace_id, job_id=job_id)
    r = b.root(metrics={"pages": 6, "answer_pages": 5, "score": score})
    e = b.stage("extract", r, 0.0, 0.3, metrics={"pages": 6, "answer_pages_detected": 5})
    b.chat("extract", e, 0.05, 0.2)
    s = b.stage("structure", r, 0.3, 0.5, metrics={"pages_mapped": 5, "items": 5})
    b.chat("structure", s, 0.35, 0.4, model="mock/strong")
    sc = b.stage(
        "score", r, 0.8, 0.6, metrics={"score": score, "disagreement": 2.0, "disagreement_flag": 0}
    )
    b.chat("score", sc, 0.85, 0.25, model="mock/strong")
    b.chat("score", sc, 1.1, 0.25, model="mock/judge")
    return b.build()


def failed_trace(trace_id: str = "b" * 32) -> dict[str, Any]:
    b = TraceBuilder(trace_id, job_id="job-2")
    r = b.root(status="ERROR", metrics={"pages": 6, "answer_pages": 5})
    e = b.stage("extract", r, 0.0, 0.3, metrics={"pages": 6, "answer_pages_detected": 5})
    b.chat("extract", e, 0.05, 0.2)
    s = b.stage("structure", r, 0.3, 3.0, status="ERROR")
    for i in range(1, 4):
        b.chat(
            "structure",
            s,
            0.3 + i * 0.5,
            0.1,
            attempt=i,
            status="ERROR",
            reason="server_error",
            parse_ok=None,
        )
    return b.build()


def missing_page_trace(trace_id: str = "c" * 32) -> dict[str, Any]:
    t = normal_trace(trace_id, job_id="job-3")
    for s in t["spans"]:
        if s["name"] == "stage structure":
            s["attributes"][attrs.METRIC_PREFIX + "pages_mapped"] = 3
    return t


def baselines(
    stage_duration_ms: float = 400, score: float = 75.0, samples: int = 50
) -> dict[str, Any]:
    def st(name: str, metrics: dict[str, float]) -> dict[str, Any]:
        return {
            "stage": name,
            "medianDurationMs": stage_duration_ms,
            "medianCostUsd": 0.001,
            "samples": samples,
            "medianMetrics": metrics,
        }

    return {
        "projectId": "demo",
        "window": 200,
        "traces": samples,
        "stages": {
            "extract": st("extract", {"pages": 6, "answer_pages_detected": 5}),
            "structure": st("structure", {"pages_mapped": 5}),
            "score": st("score", {"score": score, "disagreement": 2.0, "disagreement_flag": 0}),
        },
    }
