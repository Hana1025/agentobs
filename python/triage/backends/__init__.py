from __future__ import annotations

import os

from triage.config import TriageConfig
from triage.models import Decider


def make_decider(cfg: TriageConfig, *, offline: bool = False) -> Decider:
    """按配置建 Decider；llm_json 没有 API key（或 offline）时自动用 mock。"""
    if cfg.backend == "rules":
        from triage.backends.rules_backend import RulesDecider

        return RulesDecider(cfg)
    if cfg.backend == "llm_json":
        from triage.backends.llm_json import LLMJsonDecider, MockLLMJsonDecider

        api_key = os.environ.get("LLM_API_KEY", "")
        if offline or not api_key:
            return MockLLMJsonDecider()
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key, base_url=os.environ.get("LLM_BASE_URL") or None, max_retries=0
        )
        return LLMJsonDecider(client, model=os.environ.get("S1_MODEL") or "")
    if cfg.backend == "systemone_http":
        from triage.backends.systemone_http import SystemOneHttpDecider

        return SystemOneHttpDecider(os.environ.get("SYSTEMONE_URL", ""))
    raise ValueError(f"unknown backend {cfg.backend}")
