from __future__ import annotations

import pytest

from eval.dataset import EvalCase, build_offline_dataset, fetch_eval_cases_from_server
from eval.metrics import CaseResult, compute, markdown_summary
from eval.run import all_s2_cost, check_gates, evaluate_backend
from triage.config import TriageConfig

GATES = {"fault_recall_min": {"rules": 0.6, "llm_json": 0.9}, "normal_false_positive_max": 0.1}


def _r(exp, pred, conf, ft="none", route="pass", rc=None, s2=None):
    return CaseResult(ft, exp, None, rc, pred, conf, route, None, s2_root_cause=s2)


def test_metrics_basic():
    rs = [
        _r("normal", "normal", 0.95),
        _r("normal", "suspicious", 0.6),
        _r(
            "suspicious",
            "suspicious",
            0.8,
            ft="missing_page",
            route="escalate",
            rc="missing_or_misclassified_input",
            s2="missing_or_misclassified_input",
        ),
        _r(
            "failed",
            "suspicious",
            0.7,
            ft="provider_failure",
            route="escalate",
            rc="provider_failure",
            s2="other",
        ),
        _r("suspicious", "normal", 0.9, ft="score_outlier"),
    ]
    m = compute(rs, bins=10)
    assert m["n"] == 5
    assert m["fault_recall"] == pytest.approx(2 / 3)
    assert m["normal_false_positive_rate"] == pytest.approx(0.5)
    assert m["recall_by_fault_type"]["score_outlier"] == 0.0
    assert m["s2_root_cause_accuracy"] == 0.5
    assert 0 <= m["ece"] <= 1
    assert len(m["reliability"]) == 10
    assert m["coverage_accuracy"][-1]["coverage"] == 1.0
    assert m["routes"]["escalate"] == 2


def test_gates_per_backend():
    assert (
        check_gates({"rules": {"fault_recall": 0.75, "normal_false_positive_rate": 0.05}}, GATES)
        == []
    )
    assert check_gates({"rules": {"fault_recall": 0.5, "normal_false_positive_rate": 0.05}}, GATES)
    assert check_gates(
        {"llm_json": {"fault_recall": 0.85, "normal_false_positive_rate": 0.05}}, GATES
    )
    assert check_gates(
        {"llm_json": {"fault_recall": 0.95, "normal_false_positive_rate": 0.2}}, GATES
    )


@pytest.fixture(scope="module")
def dataset():
    return build_offline_dataset(size=120, seed=7)


def test_offline_dataset_has_labels_and_no_content(dataset):
    assert len(dataset) == 120
    assert {c.expected_label for c in dataset} >= {"normal", "suspicious", "failed"}
    for c in dataset:
        assert "agentobs.content" not in str(c.state_snapshot)
        assert c.trace is not None and c.trace["summary"]["hasRoot"]


def test_timeout_cascade_is_slow_but_not_failed(dataset):
    cases = [c for c in dataset if c.fault_type == "timeout_cascade"]
    assert cases
    for c in cases:
        assert c.state_snapshot["job"]["status"] == "OK"
        assert any(st.get("duration_ratio", 0) > 3 for st in c.state_snapshot["stages"].values())


def test_rules_and_mock_backends_pass_gates_on_offline_dataset(dataset):
    metrics = {}
    for backend in ("rules", "llm_json"):
        m, version = evaluate_backend(
            TriageConfig.load(), backend, dataset, offline=True, with_s2=True
        )
        assert version.startswith(backend) or version.startswith("llm_json_mock")
        assert m["s2_evaluated"] > 0
        assert m["s2_root_cause_accuracy"] >= 0.8, (backend, m["s2_root_cause_accuracy"])
        metrics[backend] = m
    assert check_gates(metrics, GATES) == [], {
        b: m["recall_by_fault_type"] for b, m in metrics.items()
    }
    # rules 抓不到的两类，mock S1 能抓到：这就是 S1 的增量价值
    assert (
        metrics["rules"]["recall_by_fault_type"]["scratch_as_answer"]
        < metrics["llm_json"]["recall_by_fault_type"]["scratch_as_answer"]
    )


def test_removing_builtin_rule_breaks_gate(dataset, monkeypatch):
    """故意删掉一条内置规则（job_failed）时回归门失败。"""
    import triage.rules as rules

    orig = rules.builtin_rules

    def without_job_failed(state, t):
        return [f for f in orig(state, t) if f.id != "job_failed"]

    monkeypatch.setattr(rules, "builtin_rules", without_job_failed)
    cfg = TriageConfig.load()
    # 同时关掉其他可能兜底的规则，确保 provider_failure 漏掉。slow_stage 依赖真实耗时，
    # 不关的话失败 job 偶尔会慢到被它抓住，测试就变得不稳定
    cfg.thresholds.retries_high_min = 99
    cfg.thresholds.slow_stage_ratio = 1e9
    cfg.thresholds.cost_spike_ratio = 1e9
    m, _ = evaluate_backend(cfg, "rules", dataset, offline=True, with_s2=False)
    assert m["recall_by_fault_type"]["provider_failure"] == 0.0
    assert check_gates({"rules": m}, GATES)


def test_metrics_and_markdown_estimate_all_s2_cost_savings():
    rs = [
        CaseResult("none", "normal", None, None, "normal", 0.95, "pass", "none", cost_usd=0.001),
        CaseResult("none", "normal", None, None, "normal", 0.92, "pass", "none", cost_usd=0.001),
        CaseResult("none", "normal", None, None, "normal", 0.91, "pass", "none", cost_usd=0.001),
        CaseResult(
            "provider_failure",
            "failed",
            "infrastructure",
            "provider_failure",
            "failed",
            0.9,
            "escalate",
            "infrastructure",
            s2_root_cause="provider_failure",
            s2_confidence=0.9,
            cost_usd=0.021,
            s2_cost_usd=0.02,
        ),
    ]
    m = compute(rs, bins=10)
    assert m["s2_avg_cost_usd"] == pytest.approx(0.02)
    assert m["all_s2_estimated_cost_usd"] == pytest.approx(0.08)
    assert m["cost_total_usd"] == pytest.approx(0.024)
    assert m["cost_saving_ratio"] == pytest.approx(0.7)
    md = markdown_summary("llm_json", "v1", m, all_s2_cost=None)
    assert "估算全部交给 System 2 的成本 $0.0800" in md
    assert "节省 70.0%" in md


def test_all_s2_cost_actual_mode():
    from types import SimpleNamespace

    from tests.trace_fixtures import normal_trace
    from triage.state import extract_state

    t = normal_trace()
    st = extract_state(t, None).model_dump(exclude_none=True)
    cases = [
        EvalCase("t1", "injected", "none", "normal", None, None, st, trace=t),
        EvalCase("t2", "human_review", "other", "suspicious", None, None, st, trace=None),
    ]

    class _FakeS2:
        def analyze(self, state, spans):
            return SimpleNamespace(cost_usd=0.015)

    assert all_s2_cost(cases, offline=True) == 0.0
    assert all_s2_cost(cases, offline=False, s2=_FakeS2(), run_actual=False) is None
    assert all_s2_cost(cases, offline=False, s2=_FakeS2(), run_actual=True) == pytest.approx(0.015)


def test_fetch_eval_cases_from_server_handles_human_review_and_missing_trace():
    from tests.trace_fixtures import normal_trace
    from triage.state import extract_state

    t = normal_trace()
    st = extract_state(t, None).model_dump(exclude_none=True)

    class _FakeClient:
        def get_eval_cases(self, source=None, limit=500):
            assert source == "human_review"
            return [
                {
                    "id": 1,
                    "trace_id": "a" * 32,
                    "source": "human_review",
                    "fault_type": None,
                    "expected_label": "failed",
                    "expected_stage": "extract",
                    "expected_root_cause": "provider_failure",
                    "state_snapshot": st,
                },
                {
                    "id": 2,
                    "trace_id": "b" * 32,
                    "source": "human_review",
                    "fault_type": None,
                    "expected_label": "normal",
                    "expected_stage": None,
                    "expected_root_cause": None,
                    "state_snapshot": st,
                },
            ]

        def get_trace(self, trace_id):
            if trace_id == "a" * 32:
                return t
            return None  # 模拟 90 天后原 trace 已被清理

    cases = fetch_eval_cases_from_server(_FakeClient(), source="human_review", fetch_traces=True)
    assert len(cases) == 2
    assert cases[0].fault_type == "provider_failure" and cases[0].trace is not None
    assert cases[1].fault_type == "none" and cases[1].trace is None
