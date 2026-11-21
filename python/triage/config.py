from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

DEFAULT_PATH = Path(__file__).with_name("triage.yaml")


class Thresholds(BaseModel):
    retries_high_min: int = 2
    slow_stage_ratio: float = 3.0
    cost_spike_ratio: float = 3.0
    baseline_window: int = 200
    baseline_min_samples: int = 20


class Operand(BaseModel):
    stage: str | None = None
    metric: str | None = None
    value: float | None = None


class DeclarativeRule(BaseModel):
    id: str
    hard: bool = False
    left: Operand
    op: Literal["<", "<=", ">", ">=", "==", "!="]
    right: Operand


class PassIf(BaseModel):
    p_normal_gte: float = 0.9
    no_rules_fired: bool = True


class EscalateIf(BaseModel):
    p_failed_gte: float = 0.5
    or_hard_rule: bool = True
    or_p_suspicious_gte: float = 0.4


class Routing(BaseModel):
    pass_if: PassIf = PassIf()
    escalate_if: EscalateIf = EscalateIf()
    otherwise: Literal["alert", "pass"] = "alert"
    s2_budget_per_hour: int = 30


class Review(BaseModel):
    if_s2_confidence_lt: float = 0.7
    or_severity_gte: int = 2


class S2Config(BaseModel):
    enabled: bool = True
    include_content: bool = False


class WorkerConfig(BaseModel):
    queue: str = "job_finished"
    dlq: str = "job_finished_dlq"
    visibility_timeout_s: int = 60
    batch: int = 10
    max_reads: int = 10
    idle_sleep_s: float = 2.0


class TriageConfig(BaseModel):
    backend: Literal["rules", "llm_json", "systemone_http"] = "rules"
    pricing: str | None = None
    thresholds: Thresholds = Thresholds()
    rules: list[DeclarativeRule] = Field(default_factory=list)
    routing: Routing = Routing()
    review: Review = Review()
    s2: S2Config = S2Config()
    worker: WorkerConfig = WorkerConfig()

    @classmethod
    def load(cls, path: str | Path | None = None) -> TriageConfig:
        p = Path(path or os.environ.get("TRIAGE_CONFIG") or DEFAULT_PATH)
        with open(p, encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
        cfg = cls.model_validate(raw)
        env_backend = os.environ.get("TRIAGE_BACKEND")
        if env_backend:
            cfg.backend = env_backend  # type: ignore[assignment]
        return cfg

    def version(self, backend_name: str, backend_version: str) -> str:
        """规则 + 后端 + 阈值配置的哈希（前 16 位 hex）。"""
        payload = {
            "config": self.model_dump(exclude={"worker", "pricing"}),
            "backend": backend_name,
            "backend_version": backend_version,
        }
        h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        return f"{backend_name}-{h[:16]}"
