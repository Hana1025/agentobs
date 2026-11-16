"""模型定价：model -> USD / 1M input / output tokens。"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel

log = logging.getLogger("agentobs")


class ModelPrice(BaseModel):
    input: float  # USD per 1M input tokens
    output: float  # USD per 1M output tokens


class PricingTable:
    def __init__(self, prices: dict[str, ModelPrice] | None = None):
        self.prices = prices or {}

    @classmethod
    def load(cls, source: str | Path | dict | None) -> PricingTable:
        if source is None:
            return cls()
        try:
            if isinstance(source, dict):
                raw = source
            else:
                with open(source, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
            models = raw.get("models", raw)
            return cls({k: ModelPrice(**v) for k, v in models.items()})
        except Exception:  # noqa: BLE001
            log.warning("agentobs: failed to load pricing from %s", source, exc_info=True)
            return cls()

    def cost(self, model: str | None, input_tokens: int | None, output_tokens: int | None) -> float | None:
        if not model or model not in self.prices:
            return None
        p = self.prices[model]
        return round(((input_tokens or 0) * p.input + (output_tokens or 0) * p.output) / 1_000_000, 6)
