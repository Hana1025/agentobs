"""故障类型、标准答案和注入计划（SPEC 10.2）。"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

FAULT_TYPES: dict[str, dict[str, str | None]] = {
    "none": {"label": "normal", "stage": "none", "root_cause": None},
    "transient_rate_limit": {"label": "normal", "stage": "none", "root_cause": None},
    "provider_failure": {"label": "failed", "stage": "infrastructure", "root_cause": "provider_failure"},
    "timeout_cascade": {"label": "suspicious", "stage": None, "root_cause": "timeout_cascade"},
    "output_corruption": {"label": "suspicious", "stage": "structure", "root_cause": "output_corruption"},
    "missing_page": {
        "label": "suspicious",
        "stage": "structure",
        "root_cause": "missing_or_misclassified_input",
    },
    "scratch_as_answer": {
        "label": "suspicious",
        "stage": "extract",
        "root_cause": "missing_or_misclassified_input",
    },
    "model_disagreement": {"label": "suspicious", "stage": "score", "root_cause": "model_disagreement"},
    "score_outlier": {"label": "suspicious", "stage": "score", "root_cause": "scoring_anomaly"},
}

INJECTABLE = [k for k in FAULT_TYPES if k != "none"]
STAGES = ["extract", "structure", "score"]


@dataclass
class FaultPlan:
    fault_type: str
    stage: str  # 注入的阶段；none 时为 "none"
    # mock 行为开关
    rate_limit_first_attempt: bool = False
    all_attempts_503: bool = False
    timeout_all_attempts: bool = False
    corrupt_output: bool = False  # 第一次输出不可解析，后续带控制字符但可解析
    drop_pages: int = 0  # structure 少映射几页
    swap_scratch_for_answer: bool = False
    disagreement: float = 0.0  # score 两模型分差
    score_shift: float = 0.0  # score 整体偏移
    extra: dict[str, float] = field(default_factory=dict)

    @property
    def expected(self) -> dict[str, str | None]:
        e = dict(FAULT_TYPES[self.fault_type])
        if e["stage"] is None:
            e["stage"] = self.stage
        return e


def choose_fault(rng: random.Random, normal_ratio: float = 0.7) -> str:
    if rng.random() < normal_ratio:
        return "none"
    return rng.choice(INJECTABLE)


def plan_fault(fault_type: str, rng: random.Random) -> FaultPlan:
    match fault_type:
        case "none":
            return FaultPlan("none", "none")
        case "transient_rate_limit":
            return FaultPlan(fault_type, rng.choice(STAGES), rate_limit_first_attempt=True)
        case "provider_failure":
            return FaultPlan(fault_type, rng.choice(STAGES), all_attempts_503=True)
        case "timeout_cascade":
            return FaultPlan(fault_type, rng.choice(STAGES), timeout_all_attempts=True)
        case "output_corruption":
            return FaultPlan(fault_type, "structure", corrupt_output=True)
        case "missing_page":
            return FaultPlan(fault_type, "structure", drop_pages=rng.randint(1, 2))
        case "scratch_as_answer":
            return FaultPlan(fault_type, "extract", swap_scratch_for_answer=True)
        case "model_disagreement":
            return FaultPlan(fault_type, "score", disagreement=rng.uniform(25, 45))
        case "score_outlier":
            return FaultPlan(fault_type, "score", score_shift=-rng.uniform(45, 65))
    raise ValueError(f"unknown fault type {fault_type}")
