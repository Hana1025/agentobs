from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from tests.trace_fixtures import baselines, failed_trace, missing_page_trace, normal_trace
from triage.backends.llm_json import LLMJsonDecider, MockLLMJsonDecider, build_prompt
from triage.backends.rules_backend import RulesDecider
from triage.backends.systemone_http import SystemOneHttpDecider
from triage.config import TriageConfig
from triage.models import default_questions
from triage.state import extract_state


def _answers(decider, trace, b=None):
    st = extract_state(trace, b or baselines())
    from triage.rules import evaluate

    st.rules_fired = [f.id for f in evaluate(st, TriageConfig.load())]
    return {a.question_id: a for a in decider.decide(st, default_questions(st.stage_names()))}


def test_rules_decider_maps_rules_to_probabilities():
    d = RulesDecider(TriageConfig.load())
    a = _answers(d, normal_trace())
    assert a["health"].answer == "normal" and a["health"].confidence >= 0.9
    assert math.isclose(sum(a["health"].probs.values()), 1.0)
    a = _answers(d, failed_trace())
    assert a["health"].answer == "failed"
    assert a["suspect_stage"].answer == "infrastructure"
    assert a["severity"].answer == "3"
    a = _answers(d, missing_page_trace())
    assert a["missing_input"].answer == "yes"
    assert a["suspect_stage"].answer == "structure"


def test_mock_llm_json_catches_score_outlier_without_rules():
    d = MockLLMJsonDecider()
    a = _answers(d, normal_trace(score=20.0), baselines(score=75.0))
    assert a["health"].answer == "suspicious"
    assert a["suspect_stage"].answer == "score"
    a = _answers(d, normal_trace(score=75.0), baselines(score=75.0))
    assert a["health"].answer == "normal"


class _FakeOpenAI:
    """最小的 openai 兼容 mock：可选 logprobs。"""

    def __init__(self, letter: str, with_logprobs: bool):
        self.letter = letter
        self.with_logprobs = with_logprobs
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        if kw.get("logprobs") and not self.with_logprobs:
            raise RuntimeError("logprobs not supported")
        msg = SimpleNamespace(content=self.letter)
        lp = None
        if kw.get("logprobs"):
            top = [
                SimpleNamespace(token=self.letter, logprob=math.log(0.7)),
                SimpleNamespace(token="B", logprob=math.log(0.2)),
            ]
            lp = SimpleNamespace(content=[SimpleNamespace(top_logprobs=top)])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, logprobs=lp)], usage=SimpleNamespace(cost=0.0001)
        )


def test_llm_json_uses_logprobs_when_available():
    client = _FakeOpenAI("A", with_logprobs=True)
    d = LLMJsonDecider(client, model="m")
    st = extract_state(normal_trace(), None)
    qs = default_questions(st.stage_names())
    answers = {a.question_id: a for a in d.decide(st, qs)}
    assert answers["health"].answer == "normal"
    assert 0.7 < answers["health"].confidence < 0.8
    assert client.calls == len(qs)
    assert d.last_cost_usd > 0


def test_llm_json_falls_back_to_sampling():
    client = _FakeOpenAI("C", with_logprobs=False)
    d = LLMJsonDecider(client, model="m", samples=5)
    st = extract_state(normal_trace(), None)
    qs = default_questions(st.stage_names())
    answers = {a.question_id: a for a in d.decide(st, qs)}
    assert answers["health"].answer == "failed"  # C = 第三个选项
    assert answers["health"].confidence > 0.9
    assert client.calls == len(qs) * (1 + 5)  # 每题一次失败的 logprobs 尝试 + 5 次采样


def test_prompt_encodes_options_as_letters():
    st = extract_state(normal_trace(), None)
    prompt, mapping = build_prompt(st, default_questions(st.stage_names())[0])
    assert mapping == {"A": "normal", "B": "suspicious", "C": "failed"}
    assert "A. normal" in prompt and "agentobs.content" not in prompt


def test_systemone_stub_raises():
    with pytest.raises(NotImplementedError):
        SystemOneHttpDecider("http://x").decide(extract_state(normal_trace(), None), [])


def test_llm_json_pricing_fallback_without_provider_cost():
    from agentobs.core.pricing import PricingTable

    class _TokenOnlyOpenAI:
        def __init__(self, resp_model: str):
            self.resp_model = resp_model
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kw):
            msg = SimpleNamespace(content="A")
            top = [SimpleNamespace(token="A", logprob=math.log(0.9))]
            lp = SimpleNamespace(content=[SimpleNamespace(top_logprobs=top)])
            return SimpleNamespace(
                model=self.resp_model,
                choices=[SimpleNamespace(message=msg, logprobs=lp)],
                usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=10),
            )

    table = PricingTable.load({"models": {"vendor/small": {"input": 1.0, "output": 2.0}}})
    st = extract_state(normal_trace(), None)
    qs = default_questions(st.stage_names())
    # 供应商返回带日期后缀的 model 名时也能回退到请求的 model 计费
    d = LLMJsonDecider(
        _TokenOnlyOpenAI("vendor/small-2026-01"), model="vendor/small", pricing=table
    )
    d.decide(st, qs)
    # 每题 (1000*1.0 + 10*2.0)/1e6 = 0.00102
    assert d.last_cost_usd == pytest.approx(0.00102 * len(qs))

    # 改 pricing 字段不应影响 triage_version 哈希
    cfg = TriageConfig.load()
    v1 = cfg.version("llm_json", "1")
    cfg.pricing = "/tmp/pricing.yaml"
    assert cfg.version("llm_json", "1") == v1
