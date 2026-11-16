from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

HEALTH = ["normal", "suspicious", "failed"]
ROOT_CAUSES = [
    "input_extraction_error",
    "missing_or_misclassified_input",
    "output_corruption",
    "scoring_anomaly",
    "model_disagreement",
    "provider_failure",
    "timeout_cascade",
    "other",
]


class StageState(BaseModel):
    status: str = "UNSET"
    duration_s: float = 0.0
    llm_calls: int = 0
    retries: dict[str, int] = Field(default_factory=dict)
    parse_failures: int = 0
    control_chars: int = 0
    output_chars: int = 0
    cost_usd: float | None = None
    metrics: dict[str, float] = Field(default_factory=dict)
    duration_ratio: float | None = None
    cost_ratio: float | None = None
    baseline_metrics: dict[str, float] = Field(default_factory=dict)


class JobState(BaseModel):
    status: str = "UNSET"
    duration_s: float = 0.0
    span_count: int = 0
    error_count: int = 0
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    max_attempt: int | None = None
    metrics: dict[str, float] = Field(default_factory=dict)


class TraceState(BaseModel):
    """一条 trace 的压缩状态：只有数值和枚举，不含正文。"""

    trace_id: str
    project_id: str
    job_id: str | None = None
    job: JobState
    stages: dict[str, StageState] = Field(default_factory=dict)
    rules_fired: list[str] = Field(default_factory=list)

    def stage_names(self) -> list[str]:
        return list(self.stages.keys())


class Question(BaseModel):
    id: str
    kind: Literal["choice", "bool", "score"]
    prompt: str
    options: list[str] | None = None
    score_range: tuple[int, int] | None = None

    def choices(self) -> list[str]:
        if self.kind == "choice":
            return list(self.options or [])
        if self.kind == "bool":
            return ["yes", "no"]
        lo, hi = self.score_range or (0, 3)
        return [str(i) for i in range(lo, hi + 1)]


class Answer(BaseModel):
    question_id: str
    answer: str
    probs: dict[str, float]
    confidence: float

    @classmethod
    def from_probs(cls, qid: str, probs: dict[str, float]) -> Answer:
        total = sum(probs.values()) or 1.0
        norm = {k: v / total for k, v in probs.items()}
        best = max(norm, key=lambda k: norm[k])
        return cls(question_id=qid, answer=best, probs=norm, confidence=norm[best])


class Decider(Protocol):
    name: str
    version: str

    def decide(self, state: TraceState, questions: list[Question]) -> list[Answer]: ...


class Evidence(BaseModel):
    span_id: str
    attribute: str
    note: str


class S2Result(BaseModel):
    root_cause: str
    confidence: float
    evidence: list[Evidence] = Field(default_factory=list)
    summary: str = ""
    model: str = ""
    cost_usd: float = 0.0


class TriageResult(BaseModel):
    trace_id: str
    project_id: str
    job_id: str | None
    triage_version: str
    rules_fired: list[str]
    state: dict[str, Any]
    s1_backend: str
    s1_answers: dict[str, Any]
    s1_label: str
    s1_confidence: float
    route: str
    s2_model: str | None = None
    s2_root_cause: str | None = None
    s2_confidence: float | None = None
    s2_evidence: list[dict[str, Any]] | None = None
    s2_summary: str | None = None
    review_status: str = "none"
    triage_cost_usd: float = 0.0


def default_questions(stage_names: list[str]) -> list[Question]:
    return [
        Question(
            id="health", kind="choice", prompt="Overall health of this job run.", options=HEALTH
        ),
        Question(
            id="suspect_stage",
            kind="choice",
            prompt="Which stage is most likely at fault (infrastructure = provider/network, none = nothing wrong).",
            options=[*stage_names, "infrastructure", "none"],
        ),
        Question(
            id="missing_input", kind="bool", prompt="Was some input missing or misclassified?"
        ),
        Question(
            id="severity",
            kind="score",
            prompt="Severity from 0 (none) to 3 (critical).",
            score_range=(0, 3),
        ),
    ]
