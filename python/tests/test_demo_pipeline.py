from __future__ import annotations

import random

import pytest

from agentobs.core import attrs, llm
from demo.faults import FAULT_TYPES, plan_fault
from demo.pipeline import Pipeline, PipelineConfig
from demo.synth import make_document
from tests.conftest import finished_spans


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(llm, "backoff_s", lambda *a, **k: 0.0)


def _run(sdk, fault: str, seed: int = 7):
    pipe = Pipeline(PipelineConfig(speed=0.05, attempt_timeout_s=0.1, deadline_s=0.4))
    doc = make_document("d", random.Random(seed))
    plan = plan_fault(fault, random.Random(seed))
    err = None
    try:
        pipe.run_job("job-1", doc, plan, seed)
    except Exception as e:  # noqa: BLE001
        err = e
    spans = finished_spans(sdk)
    return spans, err


def test_normal_job_has_one_root_and_three_stages(sdk):
    spans, err = _run(sdk, "none")
    assert err is None
    roots = [s for s in spans if s.parent is None]
    assert len(roots) == 1
    stages = sorted(s.attributes[attrs.STAGE] for s in spans if s.name.startswith("stage"))
    assert stages == ["extract", "score", "structure"]
    chats = [s for s in spans if s.name.startswith("chat")]
    assert len(chats) == 4  # extract, structure, score x2
    assert roots[0].attributes[attrs.EVAL_PREFIX + "expected_label"] == "normal"


def test_provider_failure_marks_job_failed(sdk):
    spans, err = _run(sdk, "provider_failure")
    assert err is not None
    root = next(s for s in spans if s.parent is None)
    assert root.status.status_code.name == "ERROR"
    assert root.attributes[attrs.EVAL_PREFIX + "expected_root_cause"] == "provider_failure"


def test_missing_page_reports_fewer_pages_mapped(sdk):
    spans, err = _run(sdk, "missing_page")
    assert err is None
    root = next(s for s in spans if s.parent is None)
    structure = next(s for s in spans if s.name == "stage structure")
    assert (
        structure.attributes[attrs.METRIC_PREFIX + "pages_mapped"]
        < root.attributes[attrs.METRIC_PREFIX + "answer_pages"]
    )


def test_every_fault_type_runs(sdk):
    for ft in FAULT_TYPES:
        _run(sdk, ft)


def test_live_client_supports_fault_injection(sdk):
    import json
    from types import SimpleNamespace

    class _EchoClient:
        def __init__(self):
            self.calls = 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kw):
            self.calls += 1
            data = json.loads(kw["messages"][1]["content"])["data"]
            return {
                "model": kw["model"],
                "choices": [{"message": {"content": json.dumps(data)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 20},
            }

    client = _EchoClient()
    pipe = Pipeline(
        PipelineConfig(
            provider="openai-compatible",
            speed=0.01,
            attempt_timeout_s=0.5,
            deadline_s=2.0,
            live_client=client,
        )
    )
    doc = make_document("d-live", random.Random(3))
    plan = plan_fault("output_corruption", random.Random(3))
    out = pipe.run_job("job-live-corrupt", doc, plan, 3)
    assert "score" in out
    spans = finished_spans(sdk)
    # structure 第一次返回非法转义触发 parse_error 重试，第二次成功但带控制字符
    struct_chats = [
        s
        for s in spans
        if s.name.startswith("chat") and s.attributes.get(attrs.STAGE) == "structure"
    ]
    assert len(struct_chats) == 2
    assert struct_chats[0].attributes.get(attrs.RETRY_REASON) == "parse_error"
    assert struct_chats[1].attributes.get(attrs.OUTPUT_CONTROL_CHARS, 0) > 0
