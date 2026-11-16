from __future__ import annotations

import pytest

import agentobs as ao
from agentobs.core import attrs
from tests.conftest import finished_spans


def test_job_and_stage_produce_root_and_child_spans(sdk, queue):
    with ao.job("job-1", attributes={"pages": 3}) as j:
        with ao.stage("extract") as st:
            st.set_metric("pages_with_text", 2)
    spans = finished_spans(sdk)
    assert len(spans) == 2
    root = next(s for s in spans if s.parent is None)
    child = next(s for s in spans if s.parent is not None)
    assert root.name == "job job-1"
    assert root.attributes[attrs.JOB_ID] == "job-1"
    assert root.attributes[attrs.METRIC_PREFIX + "pages"] == 3
    assert root.status.status_code.name == "OK"
    assert child.attributes[attrs.STAGE] == "extract"
    assert child.attributes[attrs.METRIC_PREFIX + "pages_with_text"] == 2
    assert child.parent.span_id == root.context.span_id
    # 默认模式发了一条 job_finished
    msgs = queue.queues["job_finished"]
    assert len(msgs) == 1
    assert msgs[0].message["status"] == "succeeded"
    assert msgs[0].message["trace_id"] == j.trace_id
    assert msgs[0].message["v"] == 1


def test_job_exception_sets_error_and_reraises(sdk, queue):
    with pytest.raises(ValueError):
        with ao.job("job-2"):
            raise ValueError("boom")
    root = finished_spans(sdk)[0]
    assert root.status.status_code.name == "ERROR"
    assert queue.queues["job_finished"][0].message["status"] == "failed"


def test_queue_failure_does_not_break_caller(sdk, exporter):
    class Broken:
        def send(self, *a, **k):
            raise RuntimeError("db down")

        def read(self, *a, **k):
            return []

        def archive(self, *a, **k):
            return True

        def delete(self, *a, **k):
            return True

    ao.init(service_name="s", project_id="p", exporter=exporter, queue=Broken())
    with ao.job("job-3"):
        pass  # 不抛


def test_finish_in_transaction_mode_sends_once(sdk, queue):
    with ao.job("job-4") as j:
        j.finish_in(conn=object())
    assert len(queue.queues["job_finished"]) == 1


def test_no_content_attributes_by_default(sdk):
    with ao.job("job-5"), ao.stage("s"):
        ao.llm_call(
            lambda t: {"choices": [{"message": {"content": "secret"}}]},
            provider="mock",
            model="mock/fast",
            input_content="user prompt",
        )
    for s in finished_spans(sdk):
        assert not any(k.startswith("agentobs.content.") for k in s.attributes)


def test_content_captured_and_redacted_when_enabled(exporter, queue):
    ao.init(service_name="s", project_id="p", exporter=exporter, queue=queue, capture_content=True)
    with ao.job("job-6"), ao.stage("s"):
        ao.llm_call(
            lambda t: {"choices": [{"message": {"content": "mail me at a.b@example.com"}}]},
            provider="mock",
            model="mock/fast",
            input_content="key sk-abcdefghijklmnopqrstuvwxyz",
        )
    chat = next(s for s in finished_spans(exporter) if s.name.startswith("chat"))
    assert chat.attributes[attrs.CONTENT_OUTPUT] == "mail me at <email>"
    assert chat.attributes[attrs.CONTENT_INPUT] == "key <secret>"
