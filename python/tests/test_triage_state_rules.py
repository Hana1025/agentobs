from __future__ import annotations

import json

from tests.trace_fixtures import (
    TraceBuilder,
    baselines,
    failed_trace,
    missing_page_trace,
    normal_trace,
)
from triage.config import TriageConfig
from triage.models import Answer
from triage.router import HourlyBudget, needs_review, route
from triage.rules import Fired, evaluate
from triage.state import estimate_tokens, extract_state


def test_extract_state_aggregates_stages_and_ratios():
    st = extract_state(normal_trace(), baselines(stage_duration_ms=300))
    assert st.job.status == "OK"
    assert set(st.stages) == {"extract", "structure", "score"}
    assert st.stages["score"].llm_calls == 2
    assert st.stages["structure"].metrics["pages_mapped"] == 5
    assert st.job.metrics["answer_pages"] == 5
    assert st.stages["extract"].duration_ratio == 1.0
    assert st.stages["score"].duration_ratio == 2.0
    assert st.stages["score"].baseline_metrics["score"] == 75.0


def test_baseline_ignored_when_samples_insufficient():
    st = extract_state(normal_trace(), baselines(samples=5))
    assert st.stages["extract"].duration_ratio is None


def test_state_has_no_content_and_fits_in_2k_tokens():
    b = TraceBuilder()
    r = b.root(extra={"agentobs.content.input": "SECRET PROMPT TEXT"})
    e = b.stage("extract", r, 0, 1)
    for i in range(1, 4):
        b.chat(
            "extract",
            e,
            i * 0.2,
            0.1,
            attempt=i,
            status="ERROR" if i < 3 else "OK",
            reason="timeout" if i < 3 else None,
        )
    st = extract_state(b.build(), baselines())
    dumped = st.model_dump_json()
    assert "SECRET" not in dumped
    assert "agentobs.content" not in dumped
    assert estimate_tokens(st) < 2000
    assert st.stages["extract"].retries == {"timeout": 2}


def test_builtin_rules_fire():
    cfg = TriageConfig.load()
    failed = evaluate(extract_state(failed_trace(), None), cfg)
    ids = {f.id for f in failed}
    assert "job_failed" in ids and "retries_high" in ids
    assert any(f.hard for f in failed)

    slow = normal_trace()
    for s in slow["spans"]:
        if s["name"] == "stage structure":
            s["durationMs"] = 5000
    st = extract_state(slow, baselines(stage_duration_ms=400))
    assert "slow_stage" in {f.id for f in evaluate(st, cfg)}


def test_declarative_rule_compares_stage_and_job_metrics():
    cfg = TriageConfig.load()
    st = extract_state(missing_page_trace(), None)
    fired = evaluate(st, cfg)
    assert "missing_input_pages" in {f.id for f in fired}
    assert "missing_input_pages" not in {
        f.id for f in evaluate(extract_state(normal_trace(), None), cfg)
    }


def test_routing_thresholds():
    cfg = TriageConfig.load().routing
    ok = {
        "health": Answer.from_probs("health", {"normal": 0.95, "suspicious": 0.04, "failed": 0.01})
    }
    assert route(ok, [], cfg) == "pass"
    assert route(ok, [Fired("retries_high", hard=False)], cfg) == "alert"
    susp = {
        "health": Answer.from_probs("health", {"normal": 0.5, "suspicious": 0.45, "failed": 0.05})
    }
    assert route(susp, [], cfg) == "escalate"
    assert route(ok, [Fired("job_failed", hard=True)], cfg) == "escalate"
    mid = {
        "health": Answer.from_probs("health", {"normal": 0.7, "suspicious": 0.25, "failed": 0.05})
    }
    assert route(mid, [], cfg) == "alert"


def test_review_and_budget():
    cfg = TriageConfig.load().review
    assert needs_review(0.5, 0, cfg)
    assert not needs_review(0.9, 1, cfg)
    assert needs_review(0.9, 2, cfg)
    assert needs_review(None, 0, cfg, s2_failed=True)
    b = HourlyBudget(2, window_s=100)
    assert b.try_acquire(now=0) and b.try_acquire(now=1)
    assert not b.try_acquire(now=2)
    assert b.try_acquire(now=200)


def test_triage_version_changes_with_config():
    cfg = TriageConfig.load()
    v1 = cfg.version("rules", "1")
    cfg.thresholds.retries_high_min = 5
    assert cfg.version("rules", "1") != v1
    assert json.loads(json.dumps(cfg.model_dump()))  # 可序列化
