"""mock 模型客户端：接口形状模仿 openai 的 chat.completions.create，行为由种子和 FaultPlan 决定。"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from typing import Any

from demo.faults import FaultPlan


class MockHTTPError(Exception):
    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"mock http {status}")
        self.status_code = status
        headers = {"retry-after": str(retry_after)} if retry_after else {}
        self.response = _Resp(status, headers)


@dataclass
class _Resp:
    status_code: int
    headers: dict[str, str]


class MockTimeoutError(TimeoutError):
    pass


class MockModelCall:
    """一次 stage 内模型调用的 mock 行为。每个 stage 新建一个（attempt 计数从 1 开始）。"""

    def __init__(
        self, rng: random.Random, plan: FaultPlan, stage: str, model: str, speed: float = 1.0
    ):
        self.rng = rng
        self.plan = plan
        self.stage = stage
        self.model = model
        self.attempt = 0
        self.speed = speed  # <1 更快（CI 用）
        self.injected = plan.stage == stage

    def create(
        self, *, timeout: float, payload: dict[str, Any], base_latency: float
    ) -> dict[str, Any]:
        self.attempt += 1
        p = self.plan
        if self.injected and p.all_attempts_503:
            time.sleep(0.05 * self.speed)
            raise MockHTTPError(503)
        if self.injected and p.rate_limit_first_attempt and self.attempt == 1 and not p.consumed:
            p.consumed = True
            raise MockHTTPError(429, retry_after=0.2 * self.speed)
        if (
            self.injected
            and p.timeout_all_attempts
            and self.attempt <= p.timeout_attempts
            and not p.consumed
        ):
            if self.attempt == p.timeout_attempts:
                p.consumed = True  # 只让这个 stage 的第一个模型调用级联超时
            time.sleep(timeout)
            raise MockTimeoutError(f"mock timeout after {timeout}s")
        latency = max(0.01, self.rng.gauss(base_latency, base_latency * 0.2)) * self.speed
        time.sleep(latency)
        text = json.dumps(payload, ensure_ascii=False)
        if self.injected and p.corrupt_output:
            if self.attempt == 1:
                # 非法转义：不可解析
                text = text[: len(text) // 2] + "\\u00zz" + text[len(text) // 2 :]
            else:
                # 控制字符在字符串值里：可解析（parser 用 strict=False），但 control_chars > 0
                text = json.dumps({**payload, "note": "\x07\x1b[0m"}, ensure_ascii=False)
        in_tokens = len(text) // 3 + self.rng.randint(50, 200)
        out_tokens = len(text) // 4
        return {
            "id": f"mock-{self.rng.randrange(1 << 32):08x}",
            "model": self.model,
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": in_tokens, "completion_tokens": out_tokens},
        }
