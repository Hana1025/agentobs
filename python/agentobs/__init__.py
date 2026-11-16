"""agentobs SDK：三个上下文管理器加一个模型调用封装。

import agentobs as ao
ao.init(service_name=..., project_id=..., otlp_endpoint=...)
with ao.job("job-1") as job:
    with ao.stage("extract") as st:
        r = ao.llm_call(lambda t: client.chat.completions.create(..., timeout=t),
                        provider="openrouter", model="vendor/model-x")
        st.set_metric("pages", 3)
"""

from agentobs.core import attrs, parsers
from agentobs.core.llm import (
    LLMCallError,
    LLMResult,
    NonRetryableError,
    llm_call,
    llm_call_async,
)
from agentobs.core.pricing import PricingTable
from agentobs.core.redact import DefaultRedactor, NoopRedactor, Redactor
from agentobs.core.tracer import (
    Job,
    Stage,
    current_job,
    current_stage,
    flush,
    init,
    job,
    shutdown,
    stage,
)
from agentobs.queue.port import InMemoryQueue, QueuePort

__version__ = "0.1.0"

__all__ = [
    "DefaultRedactor",
    "InMemoryQueue",
    "Job",
    "LLMCallError",
    "LLMResult",
    "NonRetryableError",
    "NoopRedactor",
    "PgmqQueue",
    "PricingTable",
    "QueuePort",
    "Redactor",
    "Stage",
    "attrs",
    "current_job",
    "current_stage",
    "flush",
    "init",
    "job",
    "llm_call",
    "llm_call_async",
    "parsers",
    "shutdown",
    "stage",
]


def __getattr__(name: str):
    if name == "PgmqQueue":
        from agentobs.queue.pgmq import PgmqQueue

        return PgmqQueue
    raise AttributeError(name)
