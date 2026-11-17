"""初始化 SDK、包一次模型调用、上报阶段 metric。

    python -m examples.quickstart

可选环境变量：AGENTOBS_OTLP_ENDPOINT（不设则不导出 span）、AGENTOBS_QUEUE_URL（不设则不发事件）。
"""

from __future__ import annotations

import os
from typing import Any

import agentobs as ao


class MockClient:
    """形状与 openai.OpenAI 一致的假客户端。换成 OpenAI(max_retries=0) 即接真实模型。"""

    def __init__(self) -> None:
        self.chat = self
        self.completions = self

    def create(
        self, *, model: str, messages: list[dict[str, str]], timeout: float
    ) -> dict[str, Any]:
        return {
            "model": model,
            "choices": [
                {"message": {"content": '{"pages_with_text": 3}'}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 120, "completion_tokens": 12},
        }


def main() -> None:
    queue_url = os.environ.get("AGENTOBS_QUEUE_URL")
    ao.init(
        service_name="downstream-pipeline",
        project_id="downstream",
        otlp_endpoint=os.environ.get("AGENTOBS_OTLP_ENDPOINT"),  # 如 http://otel-collector:4318
        metrics_port=None,  # 设成 9464 即开放 /metrics
        capture_content=False,  # 默认不采集正文
        queue=ao.queue_from_url(queue_url) if queue_url else None,  # postgresql:// 或 amqp://
        # 也可以传 pricing.yaml 的路径
        pricing={"models": {"vendor/model-x": {"input": 1.0, "output": 4.0}}},
    )

    client = MockClient()  # 真实模型：OpenAI(max_retries=0)，必须关掉底层重试
    messages = [{"role": "user", "content": "synthetic input"}]

    with ao.job("job-0001", attributes={"pages": 3}) as job:
        with ao.stage("transcribe") as st:
            r = ao.llm_call(
                call=lambda timeout: client.chat.completions.create(
                    model="vendor/model-x", messages=messages, timeout=timeout
                ),
                provider="openrouter",
                model="vendor/model-x",
                attempt_timeout_s=60,
                deadline_s=300,
                max_attempts=3,
                parse=ao.parsers.json_object,
            )
            st.set_metric("pages_with_text", r.output["pages_with_text"])

    print(
        f"trace_id={job.trace_id} attempts={r.attempts} tokens={r.input_tokens}/{r.output_tokens}"
    )
    print(f"cost_usd={r.cost_usd} pages_with_text={r.output['pages_with_text']}")
    ao.shutdown()


if __name__ == "__main__":
    main()
