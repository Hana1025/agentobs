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
