from __future__ import annotations

import pytest

from eval.dataset import build_offline_dataset
from eval.metrics import CaseResult, compute
from eval.run import check_gates, evaluate_backend
from triage.config import TriageConfig


def _r(exp, pred, conf, ft="none", route="pass", rc=None, s2=None):
    return CaseResult(ft, exp, None, rc, pred, conf, route, None, s2_root_cause=s2)


def test_metrics_basic():
    rs = [
        _r("normal", "normal", 0.95),
        _r("normal", "suspicious", 0.6),
        _r("suspicious", "suspicious", 0.8, ft="missing_page", route="escalate", rc="missing_or_misclassified_input", s2="missing_or_misclassified_input"),
        _r("failed", "suspicious", 0.7, ft="provider_failure", route="escalate", rc="provider_failure", s2="other"),
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


def test_gates():
    gates = {"fault_recall_min": 0.9, "normal_false_positive_max": 0.1}
    assert check_gates({"rules": {"fault_recall": 0.95, "normal_false_positive_rate": 0.05}}, gates) == []
    assert check_gates({"rules": {"fault_recall": 0.5, "normal_false_positive_rate": 0.05}}, gates)


@pytest.fixture(scope="module")
def dataset():
    return build_offline_dataset(size=60, seed=7)


def test_offline_dataset_has_labels_and_no_content(dataset):
    assert len(dataset) == 60
    assert {c.expected_label for c in dataset} >= {"normal", "suspicious"}
    for c in dataset:
        assert "agentobs.content" not in str(c.state_snapshot)
        assert c.trace is not None and c.trace["summary"]["hasRoot"]


def test_rules_and_mock_backends_pass_gates_on_offline_dataset(dataset):
    for backend in ("rules", "llm_json"):
        m, version = evaluate_backend(TriageConfig.load(), backend, dataset, offline=True, with_s2=True)
        assert version.startswith(backend) or version.startswith("llm_json_mock")
        assert m["fault_recall"] >= 0.85, (backend, m["recall_by_fault_type"])
        assert m["normal_false_positive_rate"] <= 0.15, backend
        assert m["s2_evaluated"] > 0


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
    gates = {"fault_recall_min": 0.9, "normal_false_positive_max": 0.1}
    assert check_gates({"rules": m}, gates), m["recall_by_fault_type"]
