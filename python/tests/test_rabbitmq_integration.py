"""RabbitMQ 集成测试（需要 Docker）：与 pgmq 那份对齐，另外验证可见性超时与原生 DLX 死信。

事务模式只支持 pgmq；这里验证 RabbitMQ 下会明确拒绝。
"""

from __future__ import annotations

import json
import subprocess
import time

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("testcontainers")
pika = pytest.importorskip("pika")
try:
    from testcontainers.community.rabbitmq import RabbitMqContainer
except ImportError:  # 旧版 testcontainers
    from testcontainers.rabbitmq import RabbitMqContainer

import agentobs as ao  # noqa: E402
from agentobs.queue.port import JOB_FINISHED, JOB_FINISHED_DLQ, queue_from_url  # noqa: E402
from agentobs.queue.rabbitmq import (  # noqa: E402
    READ_CT_HEADER,
    RETRY_SUFFIX,
    RabbitMQQueue,
    declare_topology,
)
from tests.test_triage_worker import FakeServer  # noqa: E402
from triage.backends.rules_backend import RulesDecider  # noqa: E402
from triage.config import TriageConfig  # noqa: E402
from triage.worker import Outcome, TriageWorker  # noqa: E402

IMAGE = "rabbitmq:4.1.4-management-alpine"


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _ctl(container: RabbitMqContainer, *args: str) -> None:
    code, out = container.exec(["rabbitmqctl", *args])
    assert code == 0, out


@pytest.fixture(scope="module")
def urls():
    if not _docker_available():
        pytest.skip("docker not available")
    with RabbitMqContainer(IMAGE, username="guest", password="guest") as mq:
        host, port = mq.get_container_host_ip(), mq.get_exposed_port(5672)
        base = f"{host}:{port}/%2F"
        admin = f"amqp://guest:guest@{base}"
        declare_topology(admin)
        # 与 deploy/rabbitmq/init.sh 相同的账号与权限（SPEC 4.3）
        _ctl(mq, "add_user", "agentobs_pipeline", "p")
        _ctl(mq, "add_user", "agentobs_triage", "t")
        _ctl(mq, "set_permissions", "-p", "/", "agentobs_pipeline", "^$", r"^amq\.default$", "^$")
        _ctl(
            mq,
            "set_permissions",
            "-p",
            "/",
            "agentobs_triage",
            "^$",
            r"^amq\.default$",
            r"^job_finished(_dlq|\.retry)?$",
        )
        yield {
            "admin": admin,
            "pipeline": f"amqp://agentobs_pipeline:p@{base}",
            "triage": f"amqp://agentobs_triage:t@{base}",
        }


def _purge(admin: str) -> None:
    conn = pika.BlockingConnection(pika.URLParameters(admin))
    try:
        ch = conn.channel()
        for q in (JOB_FINISHED, JOB_FINISHED_DLQ, JOB_FINISHED + RETRY_SUFFIX):
            ch.queue_purge(q)
    finally:
        conn.close()


@pytest.fixture
def clean(urls):
    _purge(urls["admin"])
    yield urls
    _purge(urls["admin"])


def test_factory_selects_rabbitmq(clean):
    assert isinstance(queue_from_url(clean["triage"]), RabbitMQQueue)


def test_default_mode_send_and_triage_read_archive(clean):
    pipeline_q = queue_from_url(clean["pipeline"])
    triage_q = RabbitMQQueue(clean["triage"])
    try:
        pipeline_q.send(JOB_FINISHED, {"v": 1, "trace_id": "a" * 32})
        msgs = triage_q.read(JOB_FINISHED, vt_s=1, qty=10)
        assert len(msgs) == 1
        got = msgs[0]
        assert got.read_ct == 1 and got.message["trace_id"] == "a" * 32
        assert triage_q.archive(JOB_FINISHED, got.msg_id)
        assert triage_q.read(JOB_FINISHED, 1, 10) == []
    finally:
        triage_q.close()


def test_pipeline_role_cannot_read(clean):
    q = RabbitMQQueue(clean["pipeline"])
    with pytest.raises(pika.exceptions.ChannelClosedByBroker) as ei:
        q.read(JOB_FINISHED, 1, 1)
    assert ei.value.reply_code == 403
    q.close()


def test_transactional_mode_is_pgmq_only(clean):
    q = RabbitMQQueue(clean["pipeline"])
    with pytest.raises(NotImplementedError, match="only supported by the pgmq"):
        q.send(JOB_FINISHED, {"v": 1}, conn=object())
    ao.init(service_name="t", project_id="demo", queue=q)
    with pytest.raises(NotImplementedError), ao.job("job-tx") as j:
        j.finish_in(object())


def test_unarchived_message_returns_after_visibility_timeout(clean):
    queue_from_url(clean["pipeline"]).send(JOB_FINISHED, {"v": 1, "trace_id": "b" * 32})
    q = RabbitMQQueue(clean["triage"])
    try:
        first = q.read(JOB_FINISHED, vt_s=1, qty=1)
        assert [m.read_ct for m in first] == [1]
        # 不 archive：下一次 read 把它改投到 retry 队列，1 秒内不可见
        assert q.read(JOB_FINISHED, vt_s=1, qty=1) == []
        deadline = time.monotonic() + 15
        again = []
        while not again and time.monotonic() < deadline:
            time.sleep(0.5)
            again = q.read(JOB_FINISHED, vt_s=1, qty=1)
        assert len(again) == 1
        assert again[0].read_ct == 2
        assert again[0].message["trace_id"] == "b" * 32
        q.archive(JOB_FINISHED, again[0].msg_id)
    finally:
        q.close()


def test_worker_dead_letters_through_native_dlx(clean):
    """M4 的死信验收在 RabbitMQ 下：读满 max_reads 后由原生 DLX 进 job_finished_dlq。"""
    cfg = TriageConfig.load()
    cfg.worker.max_reads = 3
    cfg.worker.visibility_timeout_s = 1
    q = RabbitMQQueue(clean["triage"])
    server = FakeServer()  # trace 永远不出现 → 每次都 defer
    worker = TriageWorker(cfg, q, server, RulesDecider(cfg), s2=None)
    queue_from_url(clean["pipeline"]).send(JOB_FINISHED, {"v": 1, "trace_id": "c" * 32})

    outcomes: list[str] = []
    deadline = time.monotonic() + 30
    while Outcome.DLQ not in outcomes and time.monotonic() < deadline:
        for msg in q.read(cfg.worker.queue, cfg.worker.visibility_timeout_s, cfg.worker.batch):
            outcomes.append(worker.handle(msg))
        time.sleep(0.3)
    q.close()
    assert outcomes == [Outcome.DEFER, Outcome.DEFER, Outcome.DLQ]
    assert server.posted == []

    conn = pika.BlockingConnection(pika.URLParameters(clean["triage"]))
    try:
        ch = conn.channel()
        method, props, body = ch.basic_get(JOB_FINISHED_DLQ, auto_ack=True)
        assert method is not None
        assert json.loads(body)["trace_id"] == "c" * 32
        assert props.headers[READ_CT_HEADER] == 2  # 最后一次投递前记录的次数
        deaths = props.headers["x-death"]
        assert deaths[0]["reason"] == b"rejected" or deaths[0]["reason"] == "rejected"
        assert ch.basic_get(JOB_FINISHED, auto_ack=True)[0] is None
    finally:
        conn.close()
