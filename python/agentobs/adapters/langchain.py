"""LangChain 回调适配器（M6）：让 LangChain 里的模型调用产生和 ``ao.llm_call`` 同样属性的 span。

    import agentobs as ao
    from agentobs.adapters.langchain import AgentobsCallbackHandler

    handler = AgentobsCallbackHandler(provider="openrouter")
    with ao.job("job-1"), ao.stage("extract"):
        chain.invoke(inputs, config={"callbacks": [handler]})

说明：
- 每次模型调用一个 ``chat {model}`` span，挂在当前 ``ao.stage`` 下，属性、指标与 llm_call 共用同一套函数。
- 尝试编号：同一父 run、同一模型连续失败时递增（覆盖 ``Runnable.with_retry``），成功后清零。
  模型客户端内部的重试对回调不可见，所以仍应关闭底层重试（如 ``max_retries=0``）。
- 失败按 llm_call 的规则归类 ``agentobs.retry_reason``；可重试的失败记一次 retries 指标。
- 没有 ``parse``，所以不写 ``agentobs.output.parse_ok``（与不传 parse 的 llm_call 一致）。
- 回调里的任何异常只记日志，不影响链的执行。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult as LCResult
from opentelemetry.trace import Span

from agentobs.core.llm import (
    classify,
    compute_cost,
    count_retry,
    end_error_span,
    end_ok_span,
    fill_success_attributes,
    record_metrics,
    start_attempt_span,
)
from agentobs.core.tracer import current_stage

log = logging.getLogger("agentobs")


@dataclass
class _Run:
    span: Span | None
    started: float
    stage: str
    model: str
    key: tuple[str, str]
    input_content: str | None


class AgentobsCallbackHandler(BaseCallbackHandler):
    """把 LangChain 的 LLM / chat model 调用映射成 agentobs 的模型调用 span。"""

    raise_error = False

    def __init__(self, provider: str | None = None, model: str | None = None):
        super().__init__()
        self.provider = provider
        self.model = model
        self._runs: dict[UUID, _Run] = {}
        self._failures: dict[tuple[str, str], int] = {}

    # ---------- start ----------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        text = "\n".join(str(getattr(m, "content", m)) for batch in messages for m in batch)
        self._start(serialized, run_id, parent_run_id, metadata, kwargs, text)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._start(serialized, run_id, parent_run_id, metadata, kwargs, "\n".join(prompts))

    def _start(
        self,
        serialized: dict[str, Any] | None,
        run_id: UUID,
        parent_run_id: UUID | None,
        metadata: dict[str, Any] | None,
        kwargs: dict[str, Any],
        input_text: str,
    ) -> None:
        try:
            model = self._model_name(serialized, metadata, kwargs)
            provider = self.provider or (metadata or {}).get("ls_provider") or "langchain"
            key = (str(parent_run_id or run_id), model)
            attempt = self._failures.get(key, 0) + 1
            st = current_stage()
            span = start_attempt_span(provider, model, attempt)
            self._runs[run_id] = _Run(
                span, time.monotonic(), st.name if st else "none", model, key, input_text
            )
        except Exception:  # noqa: BLE001
            log.warning("agentobs.langchain: failed to start span", exc_info=True)

    def _model_name(
        self,
        serialized: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> str:
        if self.model:
            return self.model
        md = metadata or {}
        inv = kwargs.get("invocation_params") or {}
        ser_kwargs = (serialized or {}).get("kwargs") or {}
        for v in (
            md.get("ls_model_name"),
            inv.get("model"),
            inv.get("model_name"),
            ser_kwargs.get("model"),
            ser_kwargs.get("model_name"),
        ):
            if v:
                return str(v)
        return "unknown"

    # ---------- end ----------

    def on_llm_end(self, response: LCResult, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        try:
            info = extract_lc_response(response)
            cost = compute_cost(run.model, info)
            fill_success_attributes(run.span, info, info.get("text"), None, cost, run.input_content)
            record_metrics(run.stage, run.model, "ok", time.monotonic() - run.started, info, cost)
            self._failures.pop(run.key, None)
        except Exception:  # noqa: BLE001
            log.warning("agentobs.langchain: failed to record success", exc_info=True)
        end_ok_span(run.span)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        run = self._runs.pop(run_id, None)
        if run is None:
            return
        reason = None
        try:
            reason = classify(error)
            record_metrics(run.stage, run.model, "error", time.monotonic() - run.started, {}, None)
            self._failures[run.key] = self._failures.get(run.key, 0) + 1
            if reason is not None:
                # 可重试的失败：假定外层（with_retry）会再试一次
                count_retry(run.stage, run.model, reason)
        except Exception:  # noqa: BLE001
            log.warning("agentobs.langchain: failed to record error", exc_info=True)
        end_error_span(run.span, error, reason)


def extract_lc_response(response: LCResult) -> dict[str, Any]:
    """从 LangChain 的 LLMResult 取文本、usage、模型、finish_reason（形状与 llm.extract_response 一致）。"""
    gen = response.generations[0][0] if response.generations and response.generations[0] else None
    msg = getattr(gen, "message", None)
    usage = getattr(msg, "usage_metadata", None) or {}
    meta = getattr(msg, "response_metadata", None) or {}
    gen_info = getattr(gen, "generation_info", None) or {}
    llm_output = response.llm_output or {}
    token_usage = llm_output.get("token_usage") or meta.get("token_usage") or {}

    def first(*vals: Any) -> Any:
        return next((v for v in vals if v is not None), None)

    cost = first(token_usage.get("cost"), meta.get("cost"))
    return {
        "text": getattr(gen, "text", None),
        "model": first(meta.get("model_name"), meta.get("model"), llm_output.get("model_name")),
        "input_tokens": first(usage.get("input_tokens"), token_usage.get("prompt_tokens")),
        "output_tokens": first(usage.get("output_tokens"), token_usage.get("completion_tokens")),
        "cost": float(cost) if cost is not None else None,
        "finish_reason": first(meta.get("finish_reason"), gen_info.get("finish_reason")),
    }
