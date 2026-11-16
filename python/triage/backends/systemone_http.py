"""System One 类专用模型后端：只有类和配置位。协议字段未核实，调用时抛 NotImplementedError。"""

from __future__ import annotations

from triage.models import Answer, Question, TraceState


class SystemOneHttpDecider:
    name = "systemone_http"
    version = "0"

    def __init__(self, url: str, timeout_s: float = 30):
        self.url = url
        self.timeout_s = timeout_s

    def decide(self, state: TraceState, questions: list[Question]) -> list[Answer]:
        raise NotImplementedError(
            "systemone_http backend is a stub: the request/response protocol has not been verified. "
            "Set backend: rules or llm_json."
        )
