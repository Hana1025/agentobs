"""M6 验收：LangChain 示例链产生和 llm_call 同样属性的 span。"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langchain_core")

from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.output_parsers import StrOutputParser  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.prompts import ChatPromptTemplate  # noqa: E402

import agentobs as ao  # noqa: E402
from agentobs.adapters.langchain import AgentobsCallbackHandler  # noqa: E402
from agentobs.core import attrs, llm  # noqa: E402
from tests.conftest import finished_spans  # noqa: E402

USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
META = {"model_name": "mock/fast", "finish_reason": "stop"}


class _RateLimitedError(Exception):
    def __init__(self):
        super().__init__("http 429")
        self.status_code = 429


class FlakyChat(BaseChatModel):
    """前 fail_times 次抛 429，之后返回固定消息。"""

    fail_times: int = 1
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "flaky"

    def _generate(
        self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kw: Any
    ) -> ChatResult:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _RateLimitedError()
        msg = AIMessage(content='{"ok": true}', usage_metadata=USAGE, response_metadata=META)
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm, "backoff_s", lambda *a, **k: 0.0)


def _chat_spans(exporter):
    return sorted(
        (s for s in finished_spans(exporter) if s.name.startswith("chat ")),
        key=lambda s: s.attributes.get(attrs.ATTEMPT, 0),
    )


def test_chain_span_has_same_attributes_as_llm_call(sdk):
    # 参照：llm_call（不传 parse）
    with ao.job("j-ref"), ao.stage("extract"):
        ao.llm_call(
            lambda t: {
                "model": "mock/fast",
                "choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            provider="mock",
            model="mock/fast",
        )
    ref = _chat_spans(sdk)[0]
    sdk.clear()

    # LangChain 示例链：prompt | model | parser
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content="x", usage_metadata=USAGE, response_metadata=META)])
    )
    chain = ChatPromptTemplate.from_messages([("user", "{q}")]) | model | StrOutputParser()
    handler = AgentobsCallbackHandler(provider="mock", model="mock/fast")
    with ao.job("j-lc"), ao.stage("extract"):
        assert chain.invoke({"q": "hello"}, config={"callbacks": [handler]}) == "x"
    spans = _chat_spans(sdk)
    assert len(spans) == 1
    lc = spans[0]

    assert lc.name == ref.name == "chat mock/fast"
    assert set(lc.attributes) == set(ref.attributes)
    for key in (
        attrs.STAGE,
        attrs.GEN_AI_REQUEST_MODEL,
        attrs.GEN_AI_RESPONSE_MODEL,
        attrs.GEN_AI_USAGE_INPUT_TOKENS,
        attrs.GEN_AI_USAGE_OUTPUT_TOKENS,
        attrs.COST_USD,
        attrs.ATTEMPT,
        attrs.OUTPUT_CHARS,
        attrs.OUTPUT_CONTROL_CHARS,
    ):
        assert lc.attributes[key] == ref.attributes[key], key
    assert lc.attributes[attrs.JOB_ID] == "j-lc"
    # 挂在 stage span 下
    stage = next(s for s in finished_spans(sdk) if s.name == "stage extract")
    assert lc.parent.span_id == stage.context.span_id


def test_retry_attempts_match_llm_call(sdk):
    # 参照：llm_call 第一次 429、第二次成功
    calls = {"n": 0}

    class _HttpError(Exception):
        status_code = 429

    def call(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _HttpError("http 429")
        return {
            "model": "mock/fast",
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    with ao.job("j-ref"), ao.stage("score"):
        ao.llm_call(call, provider="mock", model="mock/fast")
    ref = _chat_spans(sdk)
    sdk.clear()

    model = FlakyChat(fail_times=1).with_retry(stop_after_attempt=3, wait_exponential_jitter=False)
    handler = AgentobsCallbackHandler(provider="mock", model="mock/fast")
    with ao.job("j-lc"), ao.stage("score"):
        out = model.invoke("hi", config={"callbacks": [handler]})
    assert out.content == '{"ok": true}'
    lc = _chat_spans(sdk)

    assert [s.attributes[attrs.ATTEMPT] for s in lc] == [1, 2]
    assert [s.status.status_code.name for s in lc] == ["ERROR", "OK"]
    assert lc[0].attributes[attrs.RETRY_REASON] == attrs.REASON_RATE_LIMITED
    for got, want in zip(lc, ref, strict=True):
        assert set(got.attributes) == set(want.attributes)


def test_content_capture_follows_sdk_setting(exporter, queue):
    ao.init(service_name="s", project_id="p", exporter=exporter, queue=queue, capture_content=True)
    model = GenericFakeChatModel(messages=iter([AIMessage(content="reach me at a.b@example.com")]))
    with ao.job("j"), ao.stage("s"):
        model.invoke(
            "my key sk-abcdefghijklmnopqrstuvwxyz",
            config={"callbacks": [AgentobsCallbackHandler()]},
        )
    chat = _chat_spans(exporter)[0]
    assert chat.attributes[attrs.CONTENT_OUTPUT] == "reach me at <email>"
    assert "<secret>" in chat.attributes[attrs.CONTENT_INPUT]
    assert chat.attributes[attrs.GEN_AI_PROVIDER_NAME] == "genericfakechatmodel"


def test_handler_errors_never_break_the_chain(sdk, monkeypatch):
    import agentobs.adapters.langchain as adapter

    def boom(*a, **k):
        raise RuntimeError("instrumentation bug")

    monkeypatch.setattr(adapter, "fill_success_attributes", boom)
    model = GenericFakeChatModel(messages=iter([AIMessage(content="ok")]))
    with ao.job("j"), ao.stage("s"):
        assert model.invoke("hi", config={"callbacks": [AgentobsCallbackHandler()]}).content == "ok"
