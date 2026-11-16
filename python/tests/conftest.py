from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import agentobs as ao


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def queue() -> ao.InMemoryQueue:
    return ao.InMemoryQueue()


@pytest.fixture
def sdk(exporter: InMemorySpanExporter, queue: ao.InMemoryQueue):
    """初始化 SDK 到内存导出器 + 内存队列；capture_content 默认 False。"""
    ao.init(
        service_name="test-service",
        project_id="test",
        exporter=exporter,
        queue=queue,
        pricing={"models": {"mock/fast": {"input": 1.0, "output": 2.0}}},
    )
    yield exporter
    ao.flush()


def finished_spans(exporter: InMemorySpanExporter):
    ao.flush()
    return list(exporter.get_finished_spans())
