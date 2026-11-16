"""rules 后端：把 rules_fired 映射成固定概率的答案。始终可用，是评测的基线。"""

from __future__ import annotations

from triage.config import TriageConfig
from triage.models import Answer, Question, TraceState
from triage.rules import evaluate


class RulesDecider:
    name = "rules"
    version = "1"

    def __init__(self, cfg: TriageConfig):
        self.cfg = cfg

    def decide(self, state: TraceState, questions: list[Question]) -> list[Answer]:
        fired = evaluate(state, self.cfg)
        ids = {f.id for f in fired}
        hard = any(f.hard for f in fired)
        soft = len(ids) - (1 if hard else 0)

        if hard:
            health = {"normal": 0.02, "suspicious": 0.08, "failed": 0.90}
        elif soft >= 2:
            health = {"normal": 0.10, "suspicious": 0.85, "failed": 0.05}
        elif soft == 1:
            health = {"normal": 0.30, "suspicious": 0.65, "failed": 0.05}
        else:
            health = {"normal": 0.95, "suspicious": 0.04, "failed": 0.01}

        # 可疑阶段：规则命中的阶段里取第一个；job_failed 且有 provider 类重试 → infrastructure
        stage_opts = next(q for q in questions if q.id == "suspect_stage").choices()
        stage_probs = {o: 0.01 for o in stage_opts}
        staged = [f for f in fired if f.stage]
        if hard and _infra_signal(state):
            stage_probs["infrastructure"] = 0.8
        elif staged:
            stage_probs[staged[0].stage] = 0.75 if staged[0].stage in stage_probs else 0.01
            for f in staged[1:]:
                if f.stage in stage_probs:
                    stage_probs[f.stage] += 0.15
        else:
            stage_probs["none"] = 0.9

        missing = "missing_input_pages" in ids
        missing_probs = {"yes": 0.85 if missing else 0.05, "no": 0.15 if missing else 0.95}

        if hard:
            sev = "3"
        elif soft >= 2:
            sev = "2"
        elif soft == 1:
            sev = "1"
        else:
            sev = "0"
        sev_probs = {s: (0.85 if s == sev else 0.05) for s in ["0", "1", "2", "3"]}

        by_id = {
            "health": health,
            "suspect_stage": stage_probs,
            "missing_input": missing_probs,
            "severity": sev_probs,
        }
        out = []
        for q in questions:
            probs = by_id.get(q.id)
            if probs is None:
                opts = q.choices()
                probs = {o: 1.0 / len(opts) for o in opts}
            out.append(Answer.from_probs(q.id, probs))
        return out


def _infra_signal(state: TraceState) -> bool:
    for st in state.stages.values():
        if any(r in st.retries for r in ("server_error", "connection", "timeout", "rate_limited")):
            return True
    return False
