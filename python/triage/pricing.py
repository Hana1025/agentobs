"""分诊模型计费兜底：优先取供应商返回的 usage.cost，没有时按 PricingTable 算。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from agentobs.core.llm import extract_response
from agentobs.core.pricing import PricingTable


def load_triage_pricing(path: str | Path | None = None) -> PricingTable:
    """按配置路径或 AGENTOBS_PRICING_FILE 加载价格表；未配置时返回空表。"""
    src = path or os.environ.get("AGENTOBS_PRICING_FILE") or None
    return PricingTable.load(src)


def resolve_usage_cost(resp: Any, model: str, pricing: PricingTable | None) -> float:
    """从 LLM 响应计算本次调用费用（USD）；供应商未报费用且价格表无该模型时返回 0.0。"""
    info = extract_response(resp)
    if info.get("cost") is not None:
        return round(float(info["cost"]), 6)
    if pricing is None:
        return 0.0
    in_tok, out_tok = info.get("input_tokens"), info.get("output_tokens")
    if in_tok is None and out_tok is None:
        return 0.0
    resp_model = info.get("model")
    if resp_model:
        c = pricing.cost(resp_model, in_tok, out_tok)
        if c is not None:
            return c
    c = pricing.cost(model, in_tok, out_tok)
    return c if c is not None else 0.0
