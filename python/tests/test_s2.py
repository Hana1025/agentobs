from __future__ import annotations

from types import SimpleNamespace

from tests.trace_fixtures import baselines, failed_trace, missing_page_trace, normal_trace
from triage.s2 import MockSystemTwo, SystemTwo, strip_spans, validate
from triage.state import extract_state


def test_validate_drops_unknown_span_ids_and_caps_confidence():
    raw = {
        "root_cause": "provider_failure",
        "confidence": 0.9,
        "evidence": [
            {"span_id": "known", "attribute": "a", "note": "n"},
            {"span_id": "ghost", "attribute": "b", "note": "x"},
        ],
        "summary": "s",
    }
    r = validate(raw, {"known"}, "m", 0.0)
    assert [e.span_id for e in r.evidence] == ["known"]
    assert r.confidence < 0.5
    r2 = validate({"root_cause": "nonsense", "confidence": "1.7"}, set(), "m", 0.0)
    assert r2.root_cause == "other" and r2.confidence == 1.0


def test_strip_spans_removes_content_by_default():
    spans = [
        {
            "spanId": "1",
            "name": "chat x",
            "attributes": {"agentobs.content.input": "secret", "agentobs.attempt": 1},
        }
    ]
    assert "agentobs.content.input" not in strip_spans(spans, False)[0]["attributes"]
    assert "agentobs.content.input" in strip_spans(spans, True)[0]["attributes"]


def test_mock_s2_cites_existing_spans():
    for trace, expected in [
        (failed_trace(), "provider_failure"),
        (missing_page_trace(), "missing_or_misclassified_input"),
    ]:
        st = extract_state(trace, baselines())
        r = MockSystemTwo().analyze(st, trace["spans"])
        assert r.root_cause == expected
        ids = {s["spanId"] for s in trace["spans"]}
        assert r.evidence and all(e.span_id in ids for e in r.evidence)


class _Client:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls += 1
        t = self.texts.pop(0)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=t))],
            usage=SimpleNamespace(cost=0.01),
        )


def test_real_s2_retries_invalid_json_once_then_falls_back():
    trace = normal_trace()
    st = extract_state(trace, None)
    c = _Client(["not json", "still not json"])
    r = SystemTwo(c, model="m").analyze(st, trace["spans"])
    assert c.calls == 2
    assert r.root_cause == "other" and r.confidence == 0.0
    assert r.cost_usd == 0.02

    sid = trace["spans"][0]["spanId"]
    c = _Client(
        [
            "garbage",
            f'{{"root_cause": "timeout_cascade", "confidence": 0.8, "evidence": [{{"span_id": "{sid}", "attribute": "x", "note": "y"}}], "summary": "ok"}}',
        ]
    )
    r = SystemTwo(c, model="m").analyze(st, trace["spans"])
    assert (
        r.root_cause == "timeout_cascade" and r.confidence == 0.8 and r.evidence[0].span_id == sid
    )
