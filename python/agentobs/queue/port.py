"""QueuePort 接口（SPEC 5.4）。v1 只有 pgmq 实现；M6 加 RabbitMQ。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

JOB_FINISHED = "job_finished"
JOB_FINISHED_DLQ = "job_finished_dlq"


@dataclass(frozen=True)
class Message:
    msg_id: int
    read_ct: int
    enqueued_at: datetime
    message: dict[str, Any]


@runtime_checkable
class QueuePort(Protocol):
    def send(self, queue: str, msg: dict[str, Any], conn: Any | None = None) -> int: ...

    def read(self, queue: str, vt_s: int, qty: int) -> list[Message]: ...

    def archive(self, queue: str, msg_id: int) -> bool: ...

    def delete(self, queue: str, msg_id: int) -> bool: ...


def dead_letter(port: QueuePort, queue: str, dlq: str, msg: Message, reason: str) -> None:
    """把消息移进死信队列。

    实现了 ``dead_letter`` 的队列（RabbitMQ：原生 DLX）由它自己处理；
    其余（pgmq、内存）发一份带 reason 的副本到 dlq，再归档原消息。
    """
    native = getattr(port, "dead_letter", None)
    if callable(native):
        native(queue, dlq, msg, reason)
        return
    payload = dict(msg.message)
    payload["reason"] = reason
    payload["read_ct"] = msg.read_ct
    port.send(dlq, payload)
    port.archive(queue, msg.msg_id)


def queue_from_url(url: str) -> QueuePort:
    """按连接串选择实现：postgresql:// → pgmq，amqp(s):// → RabbitMQ。只改配置即可切换。"""
    scheme = url.split("://", 1)[0].lower()
    if scheme in ("postgres", "postgresql"):
        from agentobs.queue.pgmq import PgmqQueue

        return PgmqQueue(url)
    if scheme in ("amqp", "amqps"):
        from agentobs.queue.rabbitmq import RabbitMQQueue

        return RabbitMQQueue(url)
    raise ValueError(f"unsupported queue url scheme: {scheme!r}")


class InMemoryQueue:
    """测试用。"""

    def __init__(self) -> None:
        self.queues: dict[str, list[Message]] = {}
        self.archived: dict[str, list[Message]] = {}
        self._seq = 0

    def send(self, queue: str, msg: dict[str, Any], conn: Any | None = None) -> int:
        self._seq += 1
        self.queues.setdefault(queue, []).append(Message(self._seq, 0, datetime.now(), dict(msg)))
        return self._seq

    def read(self, queue: str, vt_s: int, qty: int) -> list[Message]:
        items = self.queues.get(queue, [])
        out = []
        for i, m in enumerate(items[:qty]):
            bumped = Message(m.msg_id, m.read_ct + 1, m.enqueued_at, m.message)
            items[i] = bumped
            out.append(bumped)
        return out

    def archive(self, queue: str, msg_id: int) -> bool:
        items = self.queues.get(queue, [])
        for m in items:
            if m.msg_id == msg_id:
                items.remove(m)
                self.archived.setdefault(queue, []).append(m)
                return True
        return False

    def delete(self, queue: str, msg_id: int) -> bool:
        items = self.queues.get(queue, [])
        for m in items:
            if m.msg_id == msg_id:
                items.remove(m)
                return True
        return False
