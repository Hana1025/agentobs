"""LangChain 链接入：回调处理器让链里的模型调用产生和 llm_call 同样属性的 span。

python -m examples.langchain_chain
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agentobs as ao
from agentobs.adapters.langchain import AgentobsCallbackHandler


def main() -> None:
    exporter = InMemorySpanExporter()  # 示例里打印 span；实际用 otlp_endpoint=…
    ao.init(
        service_name="downstream-pipeline",
        project_id="downstream",
        exporter=exporter,
        pricing={"models": {"vendor/model-x": {"input": 1.0, "output": 4.0}}},
    )

    # 假模型，带 usage；真实链里换成 ChatOpenAI(model=…, max_retries=0) 之类
    model = GenericFakeChatModel(
        messages=iter(
            [
                AIMessage(
                    content="synthetic summary",
                    usage_metadata={"input_tokens": 42, "output_tokens": 7, "total_tokens": 49},
                    response_metadata={"model_name": "vendor/model-x", "finish_reason": "stop"},
                )
            ]
        )
    )
    chain = (
        ChatPromptTemplate.from_messages([("user", "Summarise: {text}")])
        | model
        | StrOutputParser()
    )
    handler = AgentobsCallbackHandler(provider="openrouter", model="vendor/model-x")

    with ao.job("job-lc-0001"), ao.stage("summarise"):
        out = chain.invoke({"text": "synthetic input"}, config={"callbacks": [handler]})

    ao.flush()
    for span in exporter.get_finished_spans():
        if span.name.startswith("chat "):
            print(span.name)
            for k in sorted(span.attributes):
                print(f"  {k} = {span.attributes[k]}")
    print(f"output={out}")
    ao.shutdown()


if __name__ == "__main__":
    main()
