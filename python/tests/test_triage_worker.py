from __future__ import annotations

from typing import Any

import pytest

from agentobs.queue.port import InMemoryQueue
from triage.backends.rules_backend import RulesDecider
from triage.config import TriageConfig
from triage.worker import Outcome, TriageWorker
from tests.trace_fixtures import baselines, failed_trace, normal_trace


class FakeServer:
    def __init__(self):
        self.traces: dict[str, dict[str, Any]] = {}
        self.posted: list[dict[str, Any]] = []
        self.down = False

    def get_trace(self, trace_id):
        if self.down:
            raise ConnectionError("down")
        return self.traces.get(trace_id)

    def get_baselines(self, project_id, window=200):
        return baselines()

    def post_triage(self, payload):
        key = (payload["trace_id"], payload["triage_version"])
        if any((p["trace_id"], p["triage_version"]) == key for p in self.posted):
            return "exists"
        self.posted.append(payload)
        return "created"


def _msg(trace_id: str) -> dict[str, Any]:
    return {"v": 1, "project_id": "demo", "job_id": "job-1", "trace_id": trace_id, "status": "succeeded", "finished_at": "2026-11-17T01:00:00Z"}


@pytest.fixture
def setup():
    cfg = TriageConfig.load()
    cfg.worker.max_reads = 3
    q = InMemoryQueue()
    server = FakeServer()
    w = TriageWorker(cfg, q, server, RulesDecider(cfg), s2=None)
    return cfg, q, server, w


def test_happy_path_posts_triage_and_archives(setup):
    cfg, q, server, w = setup
    server.traces["a" * 32] = normal_trace()
    q.send(cfg.worker.queue, _msg("a" * 32))
    assert w.run_once() == 1
    assert len(server.posted) == 1
    assert server.posted[0]["route"] == "pass"
    assert server.posted[0]["s1_label"] == "normal"
    assert q.queues[cfg.worker.queue] == []
    assert len(q.archived[cfg.worker.queue]) == 1


def test_trace_not_ready_is_deferred_then_processed(setup):
    """时序陷阱：span 还没入库时不归档，到达后正常处理。"""
    cfg, q, server, w = setup
    q.send(cfg.worker.queue, _msg("a" * 32))
    assert w.handle(q.read(cfg.worker.queue, 60, 1)[0]) == Outcome.DEFER
    assert server.posted == []
    assert len(q.queues[cfg.worker.queue]) == 1
    # 根 span 未结束 / 不存在 → 仍然 defer
    server.traces["a" * 32] = {"summary": {"traceId": "a" * 32, "hasRoot": False, "projectId": "demo"}, "spans": []}
    assert w.handle(q.read(cfg.worker.queue, 60, 1)[0]) == Outcome.DEFER
    server.traces["a" * 32] = normal_trace()
    assert w.handle(q.read(cfg.worker.queue, 60, 1)[0]) == Outcome.ARCHIVE
    assert len(server.posted) == 1


def test_max_reads_moves_message_to_dlq(setup):
    cfg, q, server, w = setup
    q.send(cfg.worker.queue, _msg("z" * 32))  # trace 永远不出现
    outcomes = [w.handle(q.read(cfg.worker.queue, 60, 1)[0]) for _ in range(cfg.worker.max_reads)]
    assert outcomes[:-1] == [Outcome.DEFER] * (cfg.worker.max_reads - 1)
    assert outcomes[-1] == Outcome.DLQ
    dlq = q.queues[cfg.worker.dlq]
    assert len(dlq) == 1 and dlq[0].message["reason"] == "trace_not_ready"
    assert q.queues[cfg.worker.queue] == []


def test_duplicate_delivery_does_not_duplicate_triage(setup):
    cfg, q, server, w = setup
    server.traces["a" * 32] = normal_trace()
    q.send(cfg.worker.queue, _msg("a" * 32))
    q.send(cfg.worker.queue, _msg("a" * 32))
    w.run_once()
    assert len(server.posted) == 1
    assert q.queues[cfg.worker.queue] == []


def test_server_down_is_deferred(setup):
    cfg, q, server, w = setup
    server.down = True
    q.send(cfg.worker.queue, _msg("a" * 32))
    assert w.handle(q.read(cfg.worker.queue, 60, 1)[0]) == Outcome.DEFER


def test_failed_job_escalates_and_goes_to_review_without_s2(setup):
    cfg, q, server, w = setup
    server.traces["b" * 32] = failed_trace()
    q.send(cfg.worker.queue, _msg("b" * 32))
    w.run_once()
    p = server.posted[0]
    assert p["route"] == "escalate"
    assert p["s1_label"] == "failed"
    assert "job_failed" in p["rules_fired"]
    assert p["review_status"] == "pending"


def test_s2_budget_downgrades_to_alert(setup):
    cfg, q, server, w = setup
    cfg.routing.s2_budget_per_hour = 1

    class S2:
        def analyze(self, state, spans, include_content=False):
            from triage.models import S2Result

            return S2Result(root_cause="provider_failure", confidence=0.9, model="mock", summary="x")

    w = TriageWorker(cfg, q, server, RulesDecider(cfg), s2=S2())
    for i, tid in enumerate(["b" * 32, "d" * 32]):
        server.traces[tid] = failed_trace(tid)
        q.send(cfg.worker.queue, _msg(tid))
    w.run_once()
    routes = [p["route"] for p in server.posted]
    assert routes == ["escalate", "alert"]
    assert server.posted[0]["s2_root_cause"] == "provider_failure"
