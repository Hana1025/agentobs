from __future__ import annotations

from datetime import datetime

import pytest

from agentobs.queue.port import InMemoryQueue, Message, dead_letter, queue_from_url


def test_queue_from_url_selects_backend():
    from agentobs.queue.pgmq import PgmqQueue
    from agentobs.queue.rabbitmq import RabbitMQQueue

    assert isinstance(queue_from_url("postgresql://u:p@h:5432/db"), PgmqQueue)
    assert isinstance(queue_from_url("postgres://u:p@h/db"), PgmqQueue)
    assert isinstance(queue_from_url("amqp://u:p@h:5672/%2F"), RabbitMQQueue)
    with pytest.raises(ValueError, match="unsupported"):
        queue_from_url("redis://h")


def test_dead_letter_default_copies_with_reason_and_archives():
    q = InMemoryQueue()
    q.send("main", {"trace_id": "x"})
    msg = q.read("main", 1, 1)[0]
    dead_letter(q, "main", "dlq", msg, "trace_not_ready")
    assert q.queues["main"] == []
    assert q.queues["dlq"][0].message == {
        "trace_id": "x",
        "reason": "trace_not_ready",
        "read_ct": 1,
    }


def test_dead_letter_prefers_native_implementation():
    calls = []

    class Native(InMemoryQueue):
        def dead_letter(self, queue, dlq, msg, reason):
            calls.append((queue, dlq, msg.msg_id, reason))

    q = Native()
    msg = Message(7, 3, datetime.now(), {})
    dead_letter(q, "main", "dlq", msg, "r")
    assert calls == [("main", "dlq", 7, "r")]
    assert "dlq" not in q.queues


def test_rabbitmq_send_rejects_transactional_mode_without_broker():
    from agentobs.queue.rabbitmq import RabbitMQQueue, topology

    with pytest.raises(NotImplementedError):
        RabbitMQQueue("amqp://u:p@127.0.0.1:1/%2F").send("job_finished", {}, conn=object())
    names = [n for n, _ in topology()]
    assert names == ["job_finished_dlq", "job_finished", "job_finished.retry"]
    args = dict(topology())["job_finished"]
    assert args["x-dead-letter-routing-key"] == "job_finished_dlq"
