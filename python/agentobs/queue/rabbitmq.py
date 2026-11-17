"""RabbitMQ 实现（M6）。用 pika 的同步客户端，与 pgmq 实现保持同样的语义：

- send：发布到默认交换机、routing key = 队列名，开启 publisher confirms；每次发送一个短连接。
  不支持事务模式（AMQP 无法加入调用方的数据库事务），传入 ``conn`` 会抛 NotImplementedError。
- read：``basic_get`` 手动确认。读取次数放在消息头 ``x-agentobs-read-ct`` 里，返回值与 pgmq 一致
  （第一次读到时为 1）。
- 可见性超时：RabbitMQ 没有这个概念。上一轮读到但没有 archive / dead_letter 的消息，
  会在下一次 read 时改投到 ``<queue>.retry``（按消息设置 TTL = vt），到期后由 DLX 送回主队列。
- archive / delete：都是 ack。RabbitMQ 没有归档表。
- dead_letter：``basic.reject(requeue=False)``，由主队列上配置的原生 DLX 送进死信队列。

拓扑（三个 quorum 队列）见 ``declare_topology``；部署时由 deploy/rabbitmq/init.sh 创建，
运行时账号没有 configure 权限。
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pika
from pika.adapters.blocking_connection import BlockingChannel

from agentobs.queue.port import JOB_FINISHED, JOB_FINISHED_DLQ, Message

log = logging.getLogger("agentobs")

READ_CT_HEADER = "x-agentobs-read-ct"
RETRY_SUFFIX = ".retry"
# worker 崩溃导致的反复重投由 broker 兜底：超过这个次数直接进 DLQ
BROKER_DELIVERY_LIMIT = 20


@dataclass
class _Pending:
    queue: str
    read_ct: int
    body: bytes
    properties: pika.BasicProperties


def topology(
    queue: str = JOB_FINISHED, dlq: str = JOB_FINISHED_DLQ
) -> list[tuple[str, dict[str, Any]]]:
    """返回 (队列名, arguments) 列表；init.sh 和测试都按这个声明。"""
    return [
        (dlq, {"x-queue-type": "quorum"}),
        (
            queue,
            {
                "x-queue-type": "quorum",
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": dlq,
                "x-delivery-limit": BROKER_DELIVERY_LIMIT,
            },
        ),
        (
            queue + RETRY_SUFFIX,
            {
                "x-queue-type": "quorum",
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": queue,
            },
        ),
    ]


def declare_topology(url: str, queue: str = JOB_FINISHED, dlq: str = JOB_FINISHED_DLQ) -> None:
    """用有 configure 权限的账号声明队列（测试和本地开发用）。"""
    conn = pika.BlockingConnection(pika.URLParameters(url))
    try:
        ch = conn.channel()
        for name, args in topology(queue, dlq):
            ch.queue_declare(queue=name, durable=True, arguments=args)
    finally:
        conn.close()


class RabbitMQQueue:
    native_dead_letter = True

    def __init__(self, url: str, connect_timeout_s: float = 5):
        self.url = url
        self.connect_timeout_s = connect_timeout_s
        self._conn: pika.BlockingConnection | None = None
        self._ch: BlockingChannel | None = None
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()

    # ---------- 连接 ----------

    def _params(self) -> pika.URLParameters:
        p = pika.URLParameters(self.url)
        p.socket_timeout = self.connect_timeout_s
        p.blocked_connection_timeout = 30
        return p

    def _channel(self) -> BlockingChannel:
        if self._conn is None or self._conn.is_closed or self._ch is None or self._ch.is_closed:
            self._close_quietly()
            self._conn = pika.BlockingConnection(self._params())
            self._ch = self._conn.channel()
            self._ch.confirm_delivery()
            # 旧连接上未确认的消息已被 broker 重新入队
            self._pending.clear()
        return self._ch

    def _close_quietly(self) -> None:
        try:
            if self._conn is not None and self._conn.is_open:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None
        self._ch = None

    def close(self) -> None:
        with self._lock:
            if self._ch is not None and self._ch.is_open:
                try:
                    self._release_pending(vt_s=0)
                except Exception:  # noqa: BLE001
                    log.warning("rabbitmq: failed to release pending messages", exc_info=True)
            self._close_quietly()

    # ---------- QueuePort ----------

    def send(self, queue: str, msg: dict[str, Any], conn: Any | None = None) -> int:
        if conn is not None:
            raise NotImplementedError(
                "transactional mode (job.finish_in) is only supported by the pgmq queue"
            )
        msg_id = random.getrandbits(63)
        props = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.DeliveryMode.Persistent,
            message_id=str(msg_id),
            timestamp=int(time.time()),
            headers={READ_CT_HEADER: 0},
        )
        connection = pika.BlockingConnection(self._params())
        try:
            ch = connection.channel()
            ch.confirm_delivery()
            # mandatory：队列不存在时抛 UnroutableError，而不是静默丢弃
            ch.basic_publish(
                exchange="",
                routing_key=queue,
                body=json.dumps(msg, ensure_ascii=False).encode(),
                properties=props,
                mandatory=True,
            )
        finally:
            connection.close()
        return msg_id

    def read(self, queue: str, vt_s: int, qty: int) -> list[Message]:
        with self._lock:
            ch = self._channel()
            self._release_pending(vt_s, only_queue=queue)
            out: list[Message] = []
            for _ in range(qty):
                method, props, body = ch.basic_get(queue=queue, auto_ack=False)
                if method is None:
                    break
                headers = dict(props.headers or {})
                read_ct = int(headers.get(READ_CT_HEADER, 0)) + 1
                tag = int(method.delivery_tag)
                self._pending[tag] = _Pending(queue, read_ct, body, props)
                enqueued = (
                    datetime.fromtimestamp(props.timestamp, tz=UTC)
                    if props.timestamp
                    else datetime.now(UTC)
                )
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {"_raw": body.decode("utf-8", "replace")}
                out.append(Message(tag, read_ct, enqueued, payload))
            return out

    def archive(self, queue: str, msg_id: int) -> bool:
        with self._lock:
            if self._pending.pop(msg_id, None) is None:
                return False
            self._channel().basic_ack(delivery_tag=msg_id)
            return True

    def delete(self, queue: str, msg_id: int) -> bool:
        return self.archive(queue, msg_id)

    def dead_letter(self, queue: str, dlq: str, msg: Message, reason: str) -> None:
        """原生 DLX：reject 后由 broker 路由到主队列配置的死信队列。原因只记日志和指标。"""
        with self._lock:
            if self._pending.pop(msg.msg_id, None) is None:
                raise KeyError(f"unknown delivery tag {msg.msg_id}")
            self._channel().basic_reject(delivery_tag=msg.msg_id, requeue=False)
        log.info("rabbitmq: rejected %s to DLX (%s, read_ct=%s)", msg.msg_id, reason, msg.read_ct)

    # ---------- 内部 ----------

    def _release_pending(self, vt_s: int, only_queue: str | None = None) -> None:
        """上一轮没处理完的消息：带着读取次数改投到 retry 队列，TTL 到期后回到主队列。"""
        ch = self._ch
        if ch is None:
            return
        for tag, p in list(self._pending.items()):
            if only_queue is not None and p.queue != only_queue:
                continue
            headers = dict(p.properties.headers or {})
            headers[READ_CT_HEADER] = p.read_ct
            props = pika.BasicProperties(
                content_type=p.properties.content_type,
                delivery_mode=pika.DeliveryMode.Persistent,
                message_id=p.properties.message_id,
                timestamp=p.properties.timestamp,
                headers=headers,
                expiration=str(max(0, int(vt_s * 1000))),
            )
            ch.basic_publish(
                exchange="",
                routing_key=p.queue + RETRY_SUFFIX,
                body=p.body,
                properties=props,
                mandatory=True,
            )
            ch.basic_ack(delivery_tag=tag)
            del self._pending[tag]
