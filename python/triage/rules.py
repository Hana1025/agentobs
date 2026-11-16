"""第零层确定性规则：内置规则 + 下游声明式规则。"""

from __future__ import annotations

import operator
from dataclasses import dataclass

from triage.config import DeclarativeRule, Operand, Thresholds, TriageConfig
from triage.models import TraceState

BUILTIN = ["job_failed", "retries_high", "parse_failure", "control_chars_present", "slow_stage", "cost_spike"]

_OPS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}


@dataclass(frozen=True)
class Fired:
    id: str
    hard: bool
    stage: str | None = None
    detail: str = ""


def builtin_rules(state: TraceState, t: Thresholds) -> list[Fired]:
    out: list[Fired] = []
    if state.job.status == "ERROR":
        out.append(Fired("job_failed", hard=True, detail="root span status ERROR"))
    for name, st in state.stages.items():
        total_retries = sum(st.retries.values())
        if total_retries >= t.retries_high_min:
            out.append(Fired("retries_high", hard=False, stage=name, detail=f"{total_retries} retries"))
        if st.parse_failures > 0:
            out.append(Fired("parse_failure", hard=False, stage=name, detail=f"{st.parse_failures} parse failures"))
        if st.control_chars > 0:
            out.append(Fired("control_chars_present", hard=False, stage=name, detail=f"{st.control_chars} control chars"))
        if st.duration_ratio is not None and st.duration_ratio > t.slow_stage_ratio:
            out.append(Fired("slow_stage", hard=False, stage=name, detail=f"duration_ratio={st.duration_ratio}"))
        if st.cost_ratio is not None and st.cost_ratio > t.cost_spike_ratio:
            out.append(Fired("cost_spike", hard=False, stage=name, detail=f"cost_ratio={st.cost_ratio}"))
    return out


def _resolve(op: Operand, state: TraceState) -> float | None:
    if op.value is not None:
        return float(op.value)
    if op.stage == "job":
        return state.job.metrics.get(op.metric or "")
    st = state.stages.get(op.stage or "")
    if st is None:
        return None
    return st.metrics.get(op.metric or "")


def declarative_rules(state: TraceState, rules: list[DeclarativeRule]) -> list[Fired]:
    out: list[Fired] = []
    for r in rules:
        left = _resolve(r.left, state)
        right = _resolve(r.right, state)
        if left is None or right is None:
            continue  # 缺 metric 不触发
        if _OPS[r.op](left, right):
            out.append(Fired(r.id, hard=r.hard, stage=r.left.stage, detail=f"{left} {r.op} {right}"))
    return out


def evaluate(state: TraceState, cfg: TriageConfig) -> list[Fired]:
    fired = builtin_rules(state, cfg.thresholds) + declarative_rules(state, cfg.rules)
    # 去重保持顺序
    seen: set[str] = set()
    uniq: list[Fired] = []
    for f in fired:
        if f.id not in seen:
            seen.add(f.id)
            uniq.append(f)
    return uniq
