from __future__ import annotations

import pytest

from eval.dataset import build_offline_dataset
from eval.metrics import CaseResult, compute
from eval.run import check_gates, evaluate_backend
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
    cfg.thresholds.retries_high_min = 99  # 同时让 retries 也抓不到，确保 provider_failure 漏掉
    m, _ = evaluate_backend(cfg, "rules", dataset, offline=True, with_s2=False)
    assert m["recall_by_fault_type"]["provider_failure"] == 0.0
    assert check_gates({"rules": m}, GATES)
