from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

import agentobs as ao
from agentobs.core.parsers import ParseError, json_object
from agentobs.core.redact import DefaultRedactor


def test_redactor_masks_pii_and_truncates():
    r = DefaultRedactor(max_chars=20)
    out = r.redact("call +61 412 345 678 or x@y.com key sk-ABCDEFGHIJKLMNOPQRSTUV 10.0.0.1")
    assert "<number>" in out or "<email>" in out
    assert len(out) <= 20 + 40
    assert "x@y.com" not in out


def test_json_object_parser():
    assert json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ParseError):
        json_object("[1,2]")
    with pytest.raises(ParseError):
        json_object('{"a": 1')
    assert json_object('{"a": "\x1b"}') == {"a": "\x1b"}


def test_metrics_have_no_high_cardinality_labels(sdk):
    with ao.job("job-xyz-123"), ao.stage("s"):
        ao.llm_call(lambda t: {"choices": [{"message": {"content": "x"}}]}, provider="m", model="mock/fast")
    for metric in REGISTRY.collect():
        if not metric.name.startswith("agentobs_"):
            continue
        for sample in metric.samples:
            assert "job_id" not in sample.labels
            assert "trace_id" not in sample.labels
            assert "job-xyz-123" not in sample.labels.values()


def test_jobs_in_progress_gauge(sdk):
    from agentobs.core import metrics

    with ao.job("a"):
        assert metrics.in_progress.oldest_age() >= 0
        assert metrics.jobs_in_progress._value.get() == 1
    assert metrics.jobs_in_progress._value.get() == 0
