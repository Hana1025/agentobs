"""写一条声明式分诊规则，并对一条合成的 TraceState 求值。

python -m examples.custom_rule
"""

from __future__ import annotations

import yaml

from triage.config import TriageConfig
from triage.models import JobState, StageState, TraceState
from triage.rules import evaluate

RULES_YAML = """
rules:
  - id: missing_input_pages
    hard: false
    left:  {stage: structure, metric: pages_mapped}
    op: "<"
    right: {stage: job, metric: pages}
"""


def main() -> None:
    cfg = TriageConfig.load()  # 默认 triage/triage.yaml
    cfg.rules = TriageConfig.model_validate(yaml.safe_load(RULES_YAML)).rules

    # 下游在 job 根 span 上报了 pages=12，structure 阶段只映射了 11 页
    state = TraceState(
        trace_id="0" * 32,
        project_id="downstream",
        job=JobState(status="OK", metrics={"pages": 12}),
        stages={"structure": StageState(status="OK", metrics={"pages_mapped": 11})},
    )
    fired = evaluate(state, cfg)
    for f in fired:
        print(f"fired {f.id} stage={f.stage} hard={f.hard} ({f.detail})")
    print(f"triage_version={cfg.version('rules', '1')}")


if __name__ == "__main__":
    main()
