"""评测指标：精确率/召回、故障召回、误报率、ECE、可靠性曲线、覆盖率—准确率、根因准确率、成本与延迟。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import numpy as np

HEALTH = ["normal", "suspicious", "failed"]


@dataclass
class CaseResult:
    fault_type: str
    expected_label: str
    expected_stage: str | None
    expected_root_cause: str | None
    predicted_label: str
    confidence: float
    route: str
    predicted_stage: str | None
    s2_root_cause: str | None = None
    s2_confidence: float | None = None
    cost_usd: float = 0.0
    latency_s: float = 0.0
    rules_fired: list[str] = field(default_factory=list)


def _label_ok(expected: str, predicted: str) -> bool:
    """故障召回的判定：非 normal 被判为 suspicious 或 failed 即算命中；normal 必须判 normal。"""
    if expected == "normal":
        return predicted == "normal"
    return predicted != "normal"


def compute(results: list[CaseResult], bins: int = 10) -> dict[str, Any]:
    n = len(results)
    if n == 0:
        return {"n": 0}
    y = np.array([r.expected_label for r in results])
    p = np.array([r.predicted_label for r in results])
    conf = np.array([r.confidence for r in results], dtype=float)
    correct = np.array([_label_ok(r.expected_label, r.predicted_label) for r in results])
    exact = y == p

    precision = {}
    recall = {}
    for c in HEALTH:
        tp = int(((p == c) & (y == c)).sum())
        fp = int(((p == c) & (y != c)).sum())
        fn = int(((p != c) & (y == c)).sum())
        precision[c] = tp / (tp + fp) if tp + fp else None
        recall[c] = tp / (tp + fn) if tp + fn else None

    faults = y != "normal"
    normals = y == "normal"
    fault_recall = float(correct[faults].mean()) if faults.any() else None
    normal_fpr = float((~correct[normals]).mean()) if normals.any() else None

    by_ft: dict[str, list[bool]] = defaultdict(list)
    for r, ok in zip(results, correct, strict=True):
        by_ft[r.fault_type].append(bool(ok))
    recall_by_fault_type = {k: float(np.mean(v)) for k, v in by_ft.items()}

    # ECE on exact-label correctness（置信度 = max prob）
    edges = np.linspace(0, 1, bins + 1)
    reliability = []
    ece = 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (conf > lo) & (conf <= hi) if i > 0 else (conf >= lo) & (conf <= hi)
        cnt = int(mask.sum())
        if cnt:
            acc = float(exact[mask].mean())
            avg_conf = float(conf[mask].mean())
            ece += (cnt / n) * abs(acc - avg_conf)
        else:
            acc, avg_conf = 0.0, float((lo + hi) / 2)
        reliability.append({"bin_lower": float(lo), "bin_upper": float(hi), "confidence": avg_conf, "accuracy": acc, "count": cnt})

    # 覆盖率—准确率：按置信度阈值从高到低
    order = np.argsort(-conf)
    coverage_accuracy = []
    for k in range(1, n + 1):
        idx = order[:k]
        coverage_accuracy.append({"coverage": k / n, "accuracy": float(exact[idx].mean()), "threshold": float(conf[idx[-1]])})
    if n > 50:  # 下采样到约 50 个点
        step = max(1, n // 50)
        coverage_accuracy = coverage_accuracy[::step] + [coverage_accuracy[-1]]

    # S2 根因准确率（升级样本上，有标准答案的）
    s2 = [(r.expected_root_cause, r.s2_root_cause) for r in results if r.route == "escalate" and r.expected_root_cause]
    s2_acc = float(np.mean([e == g for e, g in s2])) if s2 else None

    # 可疑阶段准确率（非 normal 样本）
    stg = [(r.expected_stage, r.predicted_stage) for r in results if r.expected_label != "normal" and r.expected_stage]
    stage_acc = float(np.mean([e == g for e, g in stg])) if stg else None

    costs = np.array([r.cost_usd for r in results])
    lat = np.array([r.latency_s for r in results])
    routes = {k: int((np.array([r.route for r in results]) == k).sum()) for k in ("pass", "alert", "escalate")}

    return {
        "n": n,
        "health_precision": precision,
        "health_recall": recall,
        "fault_recall": fault_recall,
        "normal_false_positive_rate": normal_fpr,
        "recall_by_fault_type": recall_by_fault_type,
        "exact_label_accuracy": float(exact.mean()),
        "ece": float(ece),
        "reliability": reliability,
        "coverage_accuracy": coverage_accuracy,
        "s2_root_cause_accuracy": s2_acc,
        "s2_evaluated": len(s2),
        "suspect_stage_accuracy": stage_acc,
        "routes": routes,
        "escalation_rate": routes["escalate"] / n,
        "cost_per_case_usd": float(costs.mean()),
        "cost_total_usd": float(costs.sum()),
        "latency_p50_s": float(np.percentile(lat, 50)),
        "latency_p95_s": float(np.percentile(lat, 95)),
    }


def markdown_summary(backend: str, version: str, m: dict[str, Any], all_s2_cost: float | None = None) -> str:
    pct = lambda v: "–" if v is None else f"{v * 100:.1f}%"  # noqa: E731
    lines = [
        f"## {backend} ({version})",
        "",
        f"- 样本数：{m['n']}",
        f"- 故障召回：{pct(m.get('fault_recall'))}；正常样本误报率：{pct(m.get('normal_false_positive_rate'))}",
        f"- 精确标签准确率：{pct(m.get('exact_label_accuracy'))}；ECE：{m.get('ece', 0):.3f}",
        f"- 升级率：{pct(m.get('escalation_rate'))}；S2 根因准确率：{pct(m.get('s2_root_cause_accuracy'))}（{m.get('s2_evaluated', 0)} 条）",
        f"- 可疑阶段准确率：{pct(m.get('suspect_stage_accuracy'))}",
        f"- 单条成本：${m.get('cost_per_case_usd', 0):.5f}；p50/p95 延迟：{m.get('latency_p50_s', 0):.3f}s / {m.get('latency_p95_s', 0):.3f}s",
    ]
    if all_s2_cost is not None:
        lines.append(f"- 对照：全部直接交给 System 2 的成本 ${all_s2_cost:.4f}（分层总成本 ${m.get('cost_total_usd', 0):.4f}）")
    lines += ["", "| fault_type | 召回 |", "| --- | --- |"]
    for k, v in sorted(m.get("recall_by_fault_type", {}).items()):
        lines.append(f"| {k} | {pct(v)} |")
    return "\n".join(lines)
