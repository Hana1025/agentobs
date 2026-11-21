"""make eval 入口。

python -m eval.run --backend rules --backend llm_json --offline --gate   # CI：离线数据集 + 回归门
python -m eval.run --backend rules                                       # 从 server 拉 injected 用例并写回 eval-cases / eval-runs
python -m eval.run --backend llm_json --live                             # 真实模型（手动）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from eval.dataset import (
    EvalCase,
    build_offline_dataset,
    fetch_eval_cases_from_server,
    fetch_from_server,
)
from eval.metrics import CaseResult, compute, markdown_summary
from triage.backends import make_decider
from triage.config import TriageConfig
from triage.models import Answer, TraceState, default_questions
from triage.pricing import load_triage_pricing
from triage.router import route
from triage.rules import evaluate
from triage.s2 import make_system_two

REPORTS = Path(__file__).with_name("reports")
GATES = Path(__file__).with_name("gates.yaml")


def evaluate_backend(
    cfg: TriageConfig, backend: str, cases: list[EvalCase], *, offline: bool, with_s2: bool
) -> tuple[dict[str, Any], str]:
    cfg.backend = backend  # type: ignore[assignment]
    pricing = load_triage_pricing(cfg.pricing)
    decider = make_decider(cfg, offline=offline, pricing=pricing)
    version = cfg.version(decider.name, decider.version)
    s2 = make_system_two(offline=offline, pricing=pricing) if with_s2 else None
    results: list[CaseResult] = []
    for c in cases:
        state = TraceState.model_validate(c.state_snapshot)
        fired = evaluate(state, cfg)
        state.rules_fired = [f.id for f in fired]
        t0 = time.monotonic()
        answers: dict[str, Answer] = {
            a.question_id: a for a in decider.decide(state, default_questions(state.stage_names()))
        }
        r = route(answers, fired, cfg.routing)
        cost = float(getattr(decider, "last_cost_usd", 0.0) or 0.0)
        s2_rc = s2_conf = None
        s2_cost = 0.0
        if r == "escalate" and s2 is not None and c.trace is not None:
            res = s2.analyze(state, c.trace["spans"])
            s2_rc, s2_conf, s2_cost = res.root_cause, res.confidence, res.cost_usd
            cost += s2_cost
        results.append(
            CaseResult(
                fault_type=c.fault_type,
                expected_label=c.expected_label,
                expected_stage=c.expected_stage,
                expected_root_cause=c.expected_root_cause,
                predicted_label=answers["health"].answer,
                confidence=answers["health"].confidence,
                route=r,
                predicted_stage=answers["suspect_stage"].answer,
                s2_root_cause=s2_rc,
                s2_confidence=s2_conf,
                cost_usd=cost,
                s2_cost_usd=s2_cost,
                latency_s=time.monotonic() - t0,
                rules_fired=state.rules_fired,
            )
        )
    return compute(results), version


def all_s2_cost(
    cases: list[EvalCase],
    *,
    offline: bool,
    s2: Any | None = None,
    run_actual: bool = False,
) -> float | None:
    """对照：全部直接交给 System 2 的成本。

    - offline（mock）为 0.0；
    - run_actual=True 且提供 s2 时，对每条有完整 trace 的样本实际调用 S2 累加真实费用；
    - 否则返回 None，由 markdown_summary 回退到基于实际升级样本均值推算的 all_s2_estimated_cost_usd。
    """
    if offline:
        return 0.0
    if not run_actual or s2 is None:
        return None
    total = 0.0
    ran = 0
    for c in cases:
        if c.trace is None:
            continue
        state = TraceState.model_validate(c.state_snapshot)
        res = s2.analyze(state, c.trace["spans"])
        total += float(res.cost_usd or 0.0)
        ran += 1
    return round(total, 6) if ran else None


def check_gates(all_metrics: dict[str, dict[str, Any]], gates: dict[str, Any]) -> list[str]:
    failures = []
    fr_min = gates["fault_recall_min"]
    for backend, m in all_metrics.items():
        fr, fpr = m.get("fault_recall"), m.get("normal_false_positive_rate")
        threshold = (
            fr_min.get(backend, fr_min.get("default", 0.9)) if isinstance(fr_min, dict) else fr_min
        )
        if fr is None or fr < threshold:
            failures.append(f"{backend}: fault_recall {fr} < {threshold}")
        if fpr is None or fpr > gates["normal_false_positive_max"]:
            failures.append(
                f"{backend}: normal_false_positive_rate {fpr} > {gates['normal_false_positive_max']}"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backend", action="append", default=None, help="可重复；默认按 gates.yaml")
    p.add_argument("--offline", action="store_true", help="进程内生成数据集，不连 server")
    p.add_argument("--live", action="store_true", help="允许真实模型（否则强制 mock）")
    p.add_argument("--gate", action="store_true", help="按 gates.yaml 检查门槛，不达标退出码 1")
    p.add_argument("--size", type=int, default=None)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--no-s2", action="store_true")
    p.add_argument(
        "--all-s2-actual",
        action="store_true",
        help="真实模型模式下对全量样本实跑 S2 计算精确对照成本（默认按升级样本均值推算）",
    )
    p.add_argument(
        "--source",
        choices=["traces", "human_review", "eval_cases", "all"],
        default="traces",
        help="在线数据集来源：traces(默认,injected) | human_review(人工复核) | eval_cases(全部入库) | all(traces+human_review)",
    )
    p.add_argument("--project", default="demo")
    p.add_argument("--days", type=int, default=7, help="从 server 拉取最近 N 天")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK

    gates = yaml.safe_load(GATES.read_text(encoding="utf-8"))
    backends = args.backend or gates["backends"]
    offline_models = not args.live
    base_cfg = TriageConfig.load()

    client = None
    if args.offline:
        ds = gates["dataset"]
        cases = build_offline_dataset(
            args.size or ds["size"], args.seed or ds["seed"], ds.get("normal_ratio", 0.7)
        )
    else:
        from triage.client import ServerClient

        client = ServerClient(
            os.environ.get("AGENTOBS_SERVER_URL", "http://localhost:8080"),
            os.environ.get("AGENTOBS_INTERNAL_TOKEN", ""),
        )
        to = datetime.now(UTC)
        if args.source == "traces":
            cases = fetch_from_server(
                client, args.project, (to - timedelta(days=args.days)).isoformat(), to.isoformat()
            )
            if cases:
                print(client.post_eval_cases([c.payload() for c in cases]))
        elif args.source == "human_review":
            cases = fetch_eval_cases_from_server(
                client, source="human_review", fetch_traces=not args.no_s2
            )
        elif args.source == "eval_cases":
            cases = fetch_eval_cases_from_server(client, source=None, fetch_traces=not args.no_s2)
        else:  # all: 新拉取的 injected trace + 已入库的 human_review 用例
            tr_cases = fetch_from_server(
                client, args.project, (to - timedelta(days=args.days)).isoformat(), to.isoformat()
            )
            if tr_cases:
                print(client.post_eval_cases([c.payload() for c in tr_cases]))
            hr_cases = fetch_eval_cases_from_server(
                client, source="human_review", fetch_traces=not args.no_s2
            )
            seen = {(c.trace_id, c.source) for c in tr_cases if c.trace_id}
            cases = tr_cases + [c for c in hr_cases if (c.trace_id, c.source) not in seen]
    print(f"dataset: {len(cases)} cases ({'offline' if args.offline else f'server:{args.source}'})")
    if not cases:
        print("no eval cases", file=sys.stderr)
        return 2

    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {
        "timestamp": stamp,
        "dataset_size": len(cases),
        "offline": args.offline,
        "backends": {},
    }
    md = [
        f"# eval {stamp}",
        "",
        f"样本数 {len(cases)}，{'离线' if args.offline else f'server({args.source})'} 数据集。",
        "",
    ]
    s2_baseline = (
        make_system_two(offline=offline_models, pricing=load_triage_pricing(base_cfg.pricing))
        if (args.all_s2_actual and not args.no_s2 and not offline_models)
        else None
    )
    s2_cost = all_s2_cost(
        cases, offline=offline_models, s2=s2_baseline, run_actual=args.all_s2_actual
    )
    for b in backends:
        m, version = evaluate_backend(
            TriageConfig.load(), b, cases, offline=offline_models, with_s2=not args.no_s2
        )
        report["backends"][b] = {"triage_version": version, "metrics": m}
        md.append(markdown_summary(b, version, m, s2_cost))
        md.append("")
        print(markdown_summary(b, version, m, s2_cost))
        if client is not None:
            client.post_eval_run(
                {"backend": b, "triage_version": version, "dataset_size": len(cases), "metrics": m}
            )
    (REPORTS / f"{stamp}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (REPORTS / f"{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"report: {REPORTS / (stamp + '.json')}")

    if args.gate:
        failures = check_gates({b: v["metrics"] for b, v in report["backends"].items()}, gates)
        if failures:
            print("REGRESSION GATE FAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
            return 1
        print("regression gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
