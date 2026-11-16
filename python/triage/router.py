"""路由（pass / alert / escalate）与复核判定。"""

from __future__ import annotations

import threading
import time
from collections import deque

from triage.config import Review, Routing
from triage.models import Answer
from triage.rules import Fired


def route(answers: dict[str, Answer], fired: list[Fired], cfg: Routing) -> str:
    health = answers.get("health")
    probs = health.probs if health else {}
    p_normal = probs.get("normal", 0.0)
    p_failed = probs.get("failed", 0.0)
    p_susp = probs.get("suspicious", 0.0)
    hard = any(f.hard for f in fired)

    if cfg.escalate_if.or_hard_rule and hard:
        return "escalate"
    if p_failed >= cfg.escalate_if.p_failed_gte or p_susp >= cfg.escalate_if.or_p_suspicious_gte:
        return "escalate"
    if p_normal >= cfg.pass_if.p_normal_gte and (not cfg.pass_if.no_rules_fired or not fired):
        return "pass"
    return cfg.otherwise


def needs_review(
    s2_confidence: float | None, severity: int, cfg: Review, s2_failed: bool = False
) -> bool:
    if s2_failed:
        return True
    if s2_confidence is not None and s2_confidence < cfg.if_s2_confidence_lt:
        return True
    return severity >= cfg.or_severity_gte


def severity_of(answers: dict[str, Answer]) -> int:
    a = answers.get("severity")
    if not a:
        return 0
    try:
        return int(a.answer)
    except ValueError:
        return 0


class HourlyBudget:
    """滑动一小时窗口内的 System 2 调用预算；超预算时 escalate 降级为 alert。线程安全。"""

    def __init__(self, limit: int, window_s: float = 3600):
        self.limit = limit
        self.window_s = window_s
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def try_acquire(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            while self._calls and now - self._calls[0] > self.window_s:
                self._calls.popleft()
            if len(self._calls) >= self.limit:
                return False
            self._calls.append(now)
            return True

    def used(self) -> int:
        with self._lock:
            return len(self._calls)
