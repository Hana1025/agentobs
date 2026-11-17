"""System 2：推理模型读完整 trace 做根因。

输入 TraceState + 完整 span 列表（属性已脱敏；只有配置允许时才带正文）。
输出 JSON：root_cause / confidence / evidence / summary。
- evidence 的 span_id 必须在 trace 里存在，不存在的丢弃并把 confidence 压到 0.5 以下
- JSON 解析失败重试一次，仍失败 root_cause=other、confidence=0（worker 据此进人工复核）
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from agentobs.core import attrs
from agentobs.core.parsers import ParseError, json_object
from triage.models import ROOT_CAUSES, Evidence, S2Result, TraceState

log = logging.getLogger("triage.s2")

SYSTEM_PROMPT = (
    "You are a senior reliability engineer diagnosing one failed or suspicious run of a multi-stage LLM pipeline. "
    "You get a compressed numeric state and the full span list (attributes only, no raw text unless provided). "
    "Decide the single most likely root cause from this list: "
    + ", ".join(ROOT_CAUSES)
    + ". Cite evidence as span_id + attribute + short note; only cite span_ids that appear in the input. "
    "Respond with a JSON object only: "
    '{"root_cause": "...", "confidence": 0.0-1.0, "evidence": [{"span_id": "...", "attribute": "...", "note": "..."}], "summary": "one sentence"}'
)

MAX_SPANS = 400


def strip_spans(spans: list[dict[str, Any]], include_content: bool) -> list[dict[str, Any]]:
    """只保留 S2 需要的字段；默认去掉正文属性。"""
    out = []
    for s in spans[:MAX_SPANS]:
        a = dict(s.get("attributes") or {})
        if not include_content:
            a = {k: v for k, v in a.items() if not k.startswith("agentobs.content.")}
        out.append(
            {
                "span_id": s.get("spanId"),
                "parent": s.get("parentSpanId"),
                "name": s.get("name"),
                "status": s.get("statusCode"),
                "status_message": s.get("statusMessage"),
                "duration_ms": s.get("durationMs"),
                "stage": s.get("stage"),
                "attempt": s.get("attempt"),
                "attributes": a,
            }
        )
    return out


def validate(raw: dict[str, Any], span_ids: set[str], model: str, cost: float) -> S2Result:
    rc = str(raw.get("root_cause") or "other")
    if rc not in ROOT_CAUSES:
        rc = "other"
    try:
        conf = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    evidence: list[Evidence] = []
    dropped = 0
    for e in raw.get("evidence") or []:
        if not isinstance(e, dict):
            dropped += 1
            continue
        sid = str(e.get("span_id") or "")
        if sid not in span_ids:
            dropped += 1
            continue
        evidence.append(
            Evidence(
                span_id=sid,
                attribute=str(e.get("attribute") or ""),
                note=str(e.get("note") or "")[:300],
            )
        )
    if dropped:
        conf = min(conf, 0.49)
    return S2Result(
        root_cause=rc,
        confidence=conf,
        evidence=evidence,
        summary=str(raw.get("summary") or "")[:500],
        model=model,
        cost_usd=cost,
    )


class SystemTwo:
    def __init__(self, client: Any, model: str, timeout_s: float = 120):
        self.client = client
        self.model = model
        self.timeout_s = timeout_s

    def _ask(self, state: TraceState, spans: list[dict[str, Any]]) -> tuple[str, float]:
        user = json.dumps(
            {"state": state.model_dump(exclude_none=True), "spans": spans}, ensure_ascii=False
        )
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            timeout=self.timeout_s,
        )
        text = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        cost = float(getattr(usage, "cost", 0.0) or 0.0) if usage is not None else 0.0
        return text, cost

    def analyze(
        self, state: TraceState, spans: list[dict[str, Any]], include_content: bool = False
    ) -> S2Result:
        stripped = strip_spans(spans, include_content)
        span_ids = {s.get("spanId") for s in spans}
        total_cost = 0.0
        last_err: Exception | None = None
        for _ in range(2):  # JSON 解析失败重试一次
            try:
                text, cost = self._ask(state, stripped)
                total_cost += cost
                raw = json_object(text)
                return validate(raw, span_ids, self.model, total_cost)
            except ParseError as e:
                last_err = e
                log.warning("system 2 returned invalid JSON, retrying once: %s", e)
        return S2Result(
            root_cause="other",
            confidence=0.0,
            summary=f"unparseable response: {last_err}",
            model=self.model,
            cost_usd=total_cost,
        )


class MockSystemTwo:
    """确定性 mock：按状态特征推根因，并引用真实存在的 span。"""

    model = "mock/s2"

    def analyze(
        self, state: TraceState, spans: list[dict[str, Any]], include_content: bool = False
    ) -> S2Result:
        time.sleep(0.01)
        by_stage: dict[str, list[dict[str, Any]]] = {}
        for s in spans:
            if s.get("stage"):
                by_stage.setdefault(s["stage"], []).append(s)
        root = next((s for s in spans if not s.get("parentSpanId")), None)

        def ev(stage: str, attr: str, note: str, pred=None) -> list[dict[str, Any]]:
            cands = by_stage.get(stage, [])
            if pred:
                cands = [c for c in cands if pred(c)]
            return [
                {"span_id": c.get("spanId"), "attribute": attr, "note": note} for c in cands[:2]
            ]

        raw: dict[str, Any]
        failed_stage = next((n for n, st in state.stages.items() if st.status == "ERROR"), None)
        if state.job.status == "ERROR" and failed_stage:
            st = state.stages[failed_stage]
            if "timeout" in st.retries:
                raw = {
                    "root_cause": "timeout_cascade",
                    "confidence": 0.85,
                    "evidence": ev(
                        failed_stage,
                        attrs.RETRY_REASON,
                        "attempts timed out",
                        lambda c: (
                            c.get("statusCode") == "ERROR" and c.get("name", "").startswith("chat ")
                        ),
                    ),
                    "summary": f"{failed_stage} exhausted retries on timeouts",
                }
            else:
                raw = {
                    "root_cause": "provider_failure",
                    "confidence": 0.9,
                    "evidence": ev(
                        failed_stage,
                        attrs.RETRY_REASON,
                        "provider returned errors on every attempt",
                        lambda c: (
                            c.get("statusCode") == "ERROR" and c.get("name", "").startswith("chat ")
                        ),
                    ),
                    "summary": f"provider failed in {failed_stage}",
                }
        else:
            slow = next(
                (
                    n
                    for n, st in state.stages.items()
                    if st.duration_ratio and st.duration_ratio > 3 and sum(st.retries.values()) >= 1
                ),
                None,
            )
            corrupt = next(
                (n for n, st in state.stages.items() if st.parse_failures or st.control_chars), None
            )
            structure = state.stages.get("structure")
            expected = state.job.metrics.get("answer_pages")
            score_st = state.stages.get("score")
            if slow:
                raw = {
                    "root_cause": "timeout_cascade",
                    "confidence": 0.8,
                    "evidence": ev(
                        slow,
                        attrs.RETRY_REASON,
                        "repeated timeouts inflated stage duration",
                        lambda c: c.get("attributes", {}).get(attrs.RETRY_REASON),
                    ),
                    "summary": f"{slow} slowed by timeout retries",
                }
            elif corrupt:
                raw = {
                    "root_cause": "output_corruption",
                    "confidence": 0.85,
                    "evidence": ev(
                        corrupt,
                        attrs.OUTPUT_CONTROL_CHARS,
                        "control characters / parse failure in model output",
                        lambda c: c.get("name", "").startswith("chat"),
                    ),
                    "summary": f"model output in {corrupt} was corrupted",
                }
            elif (
                structure
                and expected
                and structure.metrics.get("pages_mapped", expected) < expected
            ):
                raw = {
                    "root_cause": "missing_or_misclassified_input",
                    "confidence": 0.82,
                    "evidence": ev(
                        "structure",
                        attrs.METRIC_PREFIX + "pages_mapped",
                        "fewer pages mapped than answer pages",
                        lambda c: c.get("name", "").startswith("stage"),
                    ),
                    "summary": "structure mapped fewer pages than the input had",
                }
            elif score_st and score_st.metrics.get("disagreement_flag", 0) >= 1:
                raw = {
                    "root_cause": "model_disagreement",
                    "confidence": 0.8,
                    "evidence": ev(
                        "score",
                        attrs.METRIC_PREFIX + "disagreement",
                        "scoring models disagree",
                        lambda c: c.get("name", "").startswith("stage"),
                    ),
                    "summary": "the two scoring models disagree",
                }
            elif (
                score_st
                and score_st.baseline_metrics.get("score")
                and score_st.metrics.get("score", 1e9) < score_st.baseline_metrics["score"] * 0.6
            ):
                raw = {
                    "root_cause": "scoring_anomaly",
                    "confidence": 0.75,
                    "evidence": ev(
                        "score",
                        attrs.METRIC_PREFIX + "score",
                        "score far below project median",
                        lambda c: c.get("name", "").startswith("stage"),
                    ),
                    "summary": "score is an outlier with no other symptoms",
                }
            elif (
                score_st
                and score_st.baseline_metrics.get("score")
                and score_st.metrics.get("score", 1e9) < score_st.baseline_metrics["score"] * 0.85
            ):
                raw = {
                    "root_cause": "missing_or_misclassified_input",
                    "confidence": 0.6,
                    "evidence": ev(
                        "extract",
                        attrs.METRIC_PREFIX + "answer_pages_detected",
                        "page count matches but score depressed; likely wrong pages picked",
                        lambda c: c.get("name", "").startswith("stage"),
                    ),
                    "summary": "extract likely misclassified a page",
                }
            else:
                raw = {
                    "root_cause": "other",
                    "confidence": 0.3,
                    "evidence": [
                        {
                            "span_id": root.get("spanId") if root else "",
                            "attribute": attrs.JOB_ID,
                            "note": "no clear symptom",
                        }
                    ],
                    "summary": "no clear root cause",
                }
        return validate(raw, {s.get("spanId") for s in spans}, self.model, 0.0)


def make_system_two(*, offline: bool = False) -> SystemTwo | MockSystemTwo:
    api_key = os.environ.get("LLM_API_KEY", "")
    if offline or not api_key:
        return MockSystemTwo()
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=os.environ.get("LLM_BASE_URL") or None, max_retries=0)
    return SystemTwo(client, model=os.environ.get("S2_MODEL") or "")
