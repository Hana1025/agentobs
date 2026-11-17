"""pgmq 集成测试（需要 Docker）：默认模式、事务模式回滚、read/archive。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

testcontainers = pytest.importorskip("testcontainers")
from testcontainers.postgres import PostgresContainer  # noqa: E402

import agentobs as ao  # noqa: E402
from agentobs.queue.pgmq import PgmqQueue  # noqa: E402

DEPLOY_PG = Path(__file__).resolve().parents[2] / "deploy" / "postgres"
IMAGE = "agentobs/postgres-test:latest"


def _docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def dsn():
    if not _docker_available():
        pytest.skip("docker not available")
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE, str(DEPLOY_PG)], capture_output=True, text=True
    )
    if build.returncode != 0:
        pytest.fail(f"docker build failed:\n{build.stdout[-3000:]}\n{build.stderr[-3000:]}")
    with (
        PostgresContainer(IMAGE, username="postgres", password="postgres", dbname="agentobs")
        .with_env("AGENTOBS_SERVER_DB_PASSWORD", "s")
        .with_env("AGENTOBS_PIPELINE_DB_PASSWORD", "p")
        .with_env("AGENTOBS_TRIAGE_DB_PASSWORD", "t") as pg
    ):
        host, port = pg.get_container_host_ip(), pg.get_exposed_port(5432)
        yield {
            "pipeline": f"postgresql://agentobs_pipeline:p@{host}:{port}/agentobs",
            "triage": f"postgresql://agentobs_triage:t@{host}:{port}/agentobs",
        }


def test_default_mode_send_and_triage_read_archive(dsn):
    pipeline_q = PgmqQueue(dsn["pipeline"])
    triage_q = PgmqQueue(dsn["triage"])
    msg_id = pipeline_q.send("job_finished", {"v": 1, "trace_id": "a" * 32})
    assert msg_id > 0
    msgs = triage_q.read("job_finished", vt_s=1, qty=10)
    assert any(m.msg_id == msg_id for m in msgs)
    got = next(m for m in msgs if m.msg_id == msg_id)
    assert got.read_ct == 1 and got.message["trace_id"] == "a" * 32
    assert triage_q.archive("job_finished", msg_id)
    assert not any(m.msg_id == msg_id for m in triage_q.read("job_finished", 1, 10))


def test_pipeline_role_cannot_read(dsn):
    import psycopg

    pipeline_q = PgmqQueue(dsn["pipeline"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        pipeline_q.read("job_finished", 1, 1)


def test_transactional_mode_rolls_back_with_business_transaction(dsn):
    """事务模式：业务事务回滚时队列里没有消息。"""
    import psycopg

    ao.init(service_name="t", project_id="demo", queue=PgmqQueue(dsn["pipeline"]))
    triage_q = PgmqQueue(dsn["triage"])
    before = {m.msg_id for m in triage_q.read("job_finished", 0, 100)}

    with psycopg.connect(dsn["pipeline"]) as conn:
        with pytest.raises(RuntimeError), ao.job("job-tx") as j:
            j.finish_in(conn)  # 发送进了 conn 的事务
            raise RuntimeError("business failure -> rollback")
        conn.rollback()

    after = {m.msg_id for m in triage_q.read("job_finished", 0, 100)}
    assert after - before == set(), "rolled-back transaction must not leave a message"

    # 提交则有消息
    with psycopg.connect(dsn["pipeline"]) as conn:
        with ao.job("job-tx-ok") as j:
            mid = j.finish_in(conn)
        conn.commit()
    assert any(m.msg_id == mid for m in triage_q.read("job_finished", 0, 100))


def test_transactional_example_script_runs(dsn):
    """INTEGRATION.md 第 5 节的示例脚本：--fail 回滚后没有消息，正常运行后恰好一条。"""
    from tests.test_examples import run_example

    triage_q = PgmqQueue(dsn["triage"])

    def count() -> int:
        msgs = triage_q.read("job_finished", 0, 1000)
        return sum(1 for m in msgs if m.message.get("job_id") == "job-tx-example")

    env = {"AGENTOBS_QUEUE_URL": dsn["pipeline"]}
    r = run_example("examples.transactional_pgmq", "--fail", env=env)
    assert r.returncode == 0, r.stderr
    assert "rolled back" in r.stdout
    assert count() == 0

    r = run_example("examples.transactional_pgmq", env=env)
    assert r.returncode == 0, r.stderr
    assert "committed msg_id=" in r.stdout
    assert count() == 1


@pytest.mark.skipif(not os.environ.get("AGENTOBS_QUEUE_DSN"), reason="needs AGENTOBS_QUEUE_DSN")
def test_against_running_stack():
    q = PgmqQueue(os.environ["AGENTOBS_QUEUE_DSN"])
    assert q.send("job_finished_dlq", {"v": 1, "probe": True}) > 0
