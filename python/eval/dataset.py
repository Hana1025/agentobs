"""评测数据集。

两个来源：
- offline：在进程内跑 demo 流水线（InMemorySpanExporter），把 span 转成 server 的 trace JSON，提取状态。CI 用。
- server：拉取带 agentobs.eval.* 的 trace（demo 注入器写的标准答案），同一个状态提取器生成状态，
  POST /api/internal/eval-cases 入库。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

import agentobs as ao
from agentobs.core import attrs, llm
from demo.faults import choose_fault, plan_fault
from demo.pipeline import Pipeline, PipelineConfig
from demo.synth import make_document
from triage.state import extract_state


@dataclass
class EvalCase:
    trace_id: str | None
    source: str
    fault_type: str
    expected_label: str
    expected_stage: str | None
    expected_root_cause: str | None
    state_snapshot: dict[str, Any]
    trace: dict[str, Any] | None = None  # offline 时保留完整 trace 供 S2 用

    def payload(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "source": self.source,
            "fault_type": self.fault_type,
            "expected_label": self.expected_label,
            "expected_stage": self.expected_stage,
            "expected_root_cause": self.expected_root_cause,
            "state_snapshot": self.state_snapshot,
        }


def _ns_iso(ns: int) -> str:
    return (
        datetime.fromtimestamp(ns / 1e9, tz=UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def spans_to_trace_json(spans: list[ReadableSpan]) -> dict[str, Any]:
    """把 OTel SDK 的 ReadableSpan 列表转成 GET /api/internal/traces/{id} 的形状。"""
    out = []
    for s in spans:
        a = dict(s.attributes or {})
        ctx = s.get_span_context()
        status = {StatusCode.OK: "OK", StatusCode.ERROR: "ERROR"}.get(s.status.status_code, "UNSET")
        start, end = s.start_time or 0, s.end_time or s.start_time or 0
        out.append(
            {
                "spanId": format(ctx.span_id, "016x"),
                "parentSpanId": format(s.parent.span_id, "016x") if s.parent else None,
                "name": s.name,
                "kind": s.kind.name,
                "startTime": _ns_iso(start),
                "endTime": _ns_iso(end),
                "durationMs": (end - start) / 1e6,
                "statusCode": status,
                "statusMessage": s.status.description,
                "stage": a.get(attrs.STAGE),
                "model": a.get(attrs.GEN_AI_REQUEST_MODEL),
                "inputTokens": a.get(attrs.GEN_AI_USAGE_INPUT_TOKENS),
                "outputTokens": a.get(attrs.GEN_AI_USAGE_OUTPUT_TOKENS),
                "costUsd": a.get(attrs.COST_USD),
                "attempt": a.get(attrs.ATTEMPT),
                "attributes": {k: (list(v) if isinstance(v, tuple) else v) for k, v in a.items()},
                "events": [],
                "depth": 0,
            }
        )
    roots = [s for s in out if s["parentSpanId"] is None]
    root = roots[0] if roots else None
    trace_id = format(spans[0].get_span_context().trace_id, "032x")
    costs = [s["costUsd"] for s in out if s["costUsd"] is not None]
    return {
        "summary": {
            "traceId": trace_id,
            "projectId": "demo",
            "jobId": (root or {}).get("attributes", {}).get(attrs.JOB_ID),
            "startTime": min(s["startTime"] for s in out),
            "endTime": max(s["endTime"] for s in out),
            "hasRoot": root is not None,
            "rootStatus": root["statusCode"] if root else None,
            "spanCount": len(out),
            "errorCount": sum(1 for s in out if s["statusCode"] == "ERROR"),
            "inputTokens": sum(s["inputTokens"] or 0 for s in out),
            "outputTokens": sum(s["outputTokens"] or 0 for s in out),
            "costUsd": round(sum(costs), 6) if costs else None,
            "maxAttempt": max((s["attempt"] or 0 for s in out), default=None) or None,
        },
        "spans": out,
    }


def expected_from_root(trace: dict[str, Any]) -> dict[str, str | None] | None:
    root = next((s for s in trace["spans"] if not s["parentSpanId"]), None)
    if not root:
        return None
    a = root["attributes"]
    if attrs.EVAL_PREFIX + "fault_type" not in a:
        return None
    return {
        "fault_type": a.get(attrs.EVAL_PREFIX + "fault_type"),
        "expected_label": a.get(attrs.EVAL_PREFIX + "expected_label"),
        "expected_stage": a.get(attrs.EVAL_PREFIX + "expected_stage"),
        "expected_root_cause": a.get(attrs.EVAL_PREFIX + "expected_root_cause"),
    }


def _median_baselines(traces: list[dict[str, Any]], min_samples: int = 20) -> dict[str, Any]:
    """离线时用正常样本自己算基线（与 server BaselineService 同口径）。"""
    import statistics

    per_stage: dict[str, dict[str, list[float]]] = {}
    for t in traces:
        if t["summary"]["rootStatus"] != "OK":
            continue
        costs: dict[str, float] = {}
        for s in t["spans"]:
            if s["stage"] and s["costUsd"] is not None:
                costs[s["stage"]] = costs.get(s["stage"], 0) + float(s["costUsd"])
        for s in t["spans"]:
            if s["stage"] and s["name"].startswith("stage "):
                d = per_stage.setdefault(s["stage"], {"dur": [], "cost": []})
                d["dur"].append(s["durationMs"])
                if s["stage"] in costs:
                    d["cost"].append(costs[s["stage"]])
                for k, v in s["attributes"].items():
                    if k.startswith(attrs.METRIC_PREFIX) and isinstance(v, int | float):
                        d.setdefault("m:" + k[len(attrs.METRIC_PREFIX) :], []).append(float(v))
    stages = {}
    for name, d in per_stage.items():
        stages[name] = {
            "stage": name,
            "medianDurationMs": statistics.median(d["dur"]) if d["dur"] else None,
            "medianCostUsd": statistics.median(d["cost"]) if d["cost"] else None,
            "samples": len(d["dur"]),
            "medianMetrics": {
                k[2:]: statistics.median(v) for k, v in d.items() if k.startswith("m:")
            },
        }
    return {"projectId": "demo", "window": len(traces), "traces": len(traces), "stages": stages}


def build_offline_dataset(
    size: int, seed: int, normal_ratio: float = 0.7, speed: float = 0.02
) -> list[EvalCase]:
    """进程内跑 demo 流水线生成数据集（不需要 server / collector / API key）。"""
    exporter = InMemorySpanExporter()
    ao.init(
        service_name="eval-offline",
        project_id="demo",
        exporter=exporter,
        queue=None,
        pricing={
            "models": {
                "mock/fast": {"input": 0.1, "output": 0.4},
                "mock/strong": {"input": 2, "output": 8},
                "mock/judge": {"input": 1, "output": 4},
            }
        },
    )
    original_backoff = llm.backoff_s
    llm.backoff_s = lambda *a, **k: 0.0  # 评测不等退避
    try:
        pipe = Pipeline(PipelineConfig(speed=speed, attempt_timeout_s=0.08, deadline_s=0.3))
        rng = random.Random(seed)
        traces: list[tuple[dict[str, Any], dict[str, str | None]]] = []
        for i in range(size):
            s = rng.randrange(1 << 30)
            doc = make_document(f"doc-{i}", random.Random(s))
            ft = choose_fault(rng, normal_ratio)
            plan = plan_fault(ft, random.Random(s + 1))
            exporter.clear()
            try:
                pipe.run_job(f"job-{i:05d}", doc, plan, s + 2)
            except Exception:  # noqa: BLE001
                pass
            ao.flush()
            trace = spans_to_trace_json(list(exporter.get_finished_spans()))
            exp = expected_from_root(trace)
            if exp:
                traces.append((trace, exp))
    finally:
        llm.backoff_s = original_backoff
    baselines = _median_baselines([t for t, _ in traces])
    cases = []
    for trace, exp in traces:
        st = extract_state(trace, baselines)
        cases.append(
            EvalCase(
                trace["summary"]["traceId"],
                "injected",
                exp["fault_type"] or "none",
                exp["expected_label"] or "normal",
                exp["expected_stage"],
                exp["expected_root_cause"],
                st.model_dump(exclude_none=True),
                trace,
            )
        )
    return cases


def fetch_from_server(
    client: Any, project_id: str, from_iso: str, to_iso: str, max_pages: int = 50
) -> list[EvalCase]:
    """从 server 拉带标准答案的 trace，生成 eval_cases（source=injected）。"""
    baselines = client.get_baselines(project_id)
    cases: list[EvalCase] = []
    for page in range(max_pages):
        res = client.list_traces(project_id, from_iso, to_iso, page=page, size=100)
        if not res["items"]:
            break
        for item in res["items"]:
            trace = client.get_trace(item["traceId"])
            if not trace:
                continue
            exp = expected_from_root(trace)
            if not exp:
                continue
            st = extract_state(trace, baselines)
            cases.append(
                EvalCase(
                    trace["summary"]["traceId"],
                    "injected",
                    exp["fault_type"] or "none",
                    exp["expected_label"] or "normal",
                    exp["expected_stage"],
                    exp["expected_root_cause"],
                    st.model_dump(exclude_none=True),
                    trace,
                )
            )
        if (page + 1) * 100 >= res["total"]:
            break
    return cases
