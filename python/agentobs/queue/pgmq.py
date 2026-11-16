"""pgmq 实现。直接用 psycopg3 调 pgmq SQL 函数，不引入额外客户端库。

conn=None 时每次 send 开一个短连接（autocommit）；传入 conn 时在调用方事务里执行，
支持 psycopg Connection 和 SQLAlchemy Connection / Session。
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from agentobs.queue.port import Message

_SEND = "SELECT * FROM pgmq.send(%s, %s::jsonb)"
_READ = "SELECT msg_id, read_ct, enqueued_at, message FROM pgmq.read(%s, %s, %s)"
_ARCHIVE = "SELECT pgmq.archive(%s, %s::bigint)"
_DELETE = "SELECT pgmq.delete(%s, %s::bigint)"


class PgmqQueue:
    def __init__(self, dsn: str, connect_timeout_s: int = 5):
        self.dsn = dsn
        self.connect_timeout_s = connect_timeout_s

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, autocommit=True, connect_timeout=self.connect_timeout_s)

    def send(self, queue: str, msg: dict[str, Any], conn: Any | None = None) -> int:
        payload = json.dumps(msg, ensure_ascii=False)
        if conn is None:
            with self._connect() as c, c.cursor(row_factory=tuple_row) as cur:
                cur.execute(_SEND, (queue, payload))
                return int(cur.fetchone()[0])
        return _send_on(conn, queue, payload)

    def read(self, queue: str, vt_s: int, qty: int) -> list[Message]:
        with self._connect() as c, c.cursor(row_factory=tuple_row) as cur:
            cur.execute(_READ, (queue, vt_s, qty))
            return [
                Message(
                    int(r[0]), int(r[1]), r[2], r[3] if isinstance(r[3], dict) else json.loads(r[3])
                )
                for r in cur.fetchall()
            ]

    def archive(self, queue: str, msg_id: int) -> bool:
        with self._connect() as c, c.cursor(row_factory=tuple_row) as cur:
            cur.execute(_ARCHIVE, (queue, msg_id))
            return bool(cur.fetchone()[0])

    def delete(self, queue: str, msg_id: int) -> bool:
        with self._connect() as c, c.cursor(row_factory=tuple_row) as cur:
            cur.execute(_DELETE, (queue, msg_id))
            return bool(cur.fetchone()[0])


def _send_on(conn: Any, queue: str, payload: str) -> int:
    """在调用方的连接/会话上发送，不提交——由调用方的事务决定。"""
    if isinstance(conn, psycopg.Connection):
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute(_SEND, (queue, payload))
            return int(cur.fetchone()[0])
    # SQLAlchemy Connection / Session：都有 .execute(text, params)
    try:
        from sqlalchemy import text  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise TypeError(f"unsupported connection type {type(conn)!r}") from e
    row = conn.execute(
        text("SELECT * FROM pgmq.send(:q, CAST(:m AS jsonb))"), {"q": queue, "m": payload}
    ).fetchone()
    return int(row[0])
