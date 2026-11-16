"""状态提取器：把 server 返回的 trace（summary + spans）压缩成 TraceState。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from agentobs.core import attrs
from triage.models import JobState, StageState, TraceState

_RETRY_REASONS = set(attrs.RETRY_REASONS)


def _metrics_of(span_attrs: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in span_attrs.items():
        if (
            k.startswith(attrs.METRIC_PREFIX)
            and isinstance(v, int | float)
            and not isinstance(v, bool)
        ):
            out[k[len(attrs.METRIC_PREFIX) :]] = float(v)
    return out


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def extract_state(
    trace: dict[str, Any], baselines: dict[str, Any] | None, min_samples: int = 20
) -> TraceState:
    """``trace`` 是 GET /api/internal/traces/{id} 的 JSON；``baselines`` 是 GET /api/internal/baselines 的 JSON。"""
    summary = trace["summary"]
    spans: list[dict[str, Any]] = trace.get("spans", [])
    root = next((s for s in spans if not s.get("parentSpanId")), None)
    root_attrs = (root or {}).get("attributes") or {}

    job = JobState(
        status=summary.get("rootStatus") or "UNSET",
        duration_s=_duration_s(summary.get("startTime"), summary.get("endTime")),
        span_count=int(summary.get("spanCount") or 0),
        error_count=int(summary.get("errorCount") or 0),
        cost_usd=_f(summary.get("costUsd")),
        input_tokens=summary.get("inputTokens"),
        output_tokens=summary.get("outputTokens"),
        max_attempt=summary.get("maxAttempt"),
        metrics=_metrics_of(root_attrs),
    )

    stages: dict[str, StageState] = {}
    costs: dict[str, float] = defaultdict(float)
    has_cost: set[str] = set()
    # 先建 stage span
    for s in spans:
        if s.get("name", "").startswith("stage ") and s.get("stage"):
            st = StageState(
                status=s.get("statusCode") or "UNSET",
                duration_s=round(float(s.get("durationMs") or 0) / 1000, 3),
                metrics=_metrics_of(s.get("attributes") or {}),
            )
            stages[s["stage"]] = st
    # 再把模型调用聚合进去
    for s in spans:
        a = s.get("attributes") or {}
        stage = s.get("stage")
        if not stage or not s.get("name", "").startswith("chat "):
            continue
        st = stages.get(stage)
        if st is None:
            st = StageState(status="UNSET")
            stages[stage] = st
        st.llm_calls += 1
        reason = a.get(attrs.RETRY_REASON)
        if reason in _RETRY_REASONS:
            st.retries[reason] = st.retries.get(reason, 0) + 1
        if a.get(attrs.OUTPUT_PARSE_OK) is False:
            st.parse_failures += 1
        st.control_chars += int(a.get(attrs.OUTPUT_CONTROL_CHARS) or 0)
        st.output_chars += int(a.get(attrs.OUTPUT_CHARS) or 0)
        c = _f(s.get("costUsd"))
        if c is not None:
            costs[stage] += c
            has_cost.add(stage)
    for name, st in stages.items():
        if name in has_cost:
            st.cost_usd = round(costs[name], 6)

    # 相对基线
    if baselines:
        for name, st in stages.items():
            b = (baselines.get("stages") or {}).get(name)
            if not b or int(b.get("samples") or 0) < min_samples:
                continue
            md = _f(b.get("medianDurationMs"))
            if md and md > 0:
                st.duration_ratio = round(st.duration_s * 1000 / md, 3)
            mc = _f(b.get("medianCostUsd"))
            if mc and mc > 0 and st.cost_usd is not None:
                st.cost_ratio = round(st.cost_usd / mc, 3)
            st.baseline_metrics = {
                k: float(v) for k, v in (b.get("medianMetrics") or {}).items() if v is not None
            }

    return TraceState(
        trace_id=summary["traceId"],
        project_id=summary.get("projectId") or "default",
        job_id=summary.get("jobId"),
        job=job,
        stages=stages,
    )


def _duration_s(start: str | None, end: str | None) -> float:
    if not start or not end:
        return 0.0
    from datetime import datetime

    try:
        a = datetime.fromisoformat(start.replace("Z", "+00:00"))
        b = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return round((b - a).total_seconds(), 3)
    except ValueError:
        return 0.0


def estimate_tokens(state: TraceState) -> int:
    """估算序列化后的 token 数（优先 tiktoken，没有则按 4 字符 ≈ 1 token）。"""
    text = state.model_dump_json(exclude_none=True)
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:  # noqa: BLE001
        return len(text) // 4 + 1
