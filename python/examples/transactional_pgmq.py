"""事务模式：job_finished 事件与业务状态更新同时提交或回滚。只支持 pgmq。

    AGENTOBS_QUEUE_URL=postgresql://agentobs_pipeline:…@localhost:5432/agentobs \\
        python -m examples.transactional_pgmq [--fail]

``--fail`` 模拟业务失败：事务回滚，队列里不会出现这条消息。
示例用临时表代替下游的业务表，所以 pipeline 账号不需要建表权限。
"""

from __future__ import annotations

import os
import sys

import psycopg

import agentobs as ao


def main(argv: list[str]) -> int:
    fail = "--fail" in argv
    dsn = os.environ["AGENTOBS_QUEUE_URL"]
    ao.init(
        service_name="downstream-pipeline", project_id="downstream", queue=ao.queue_from_url(dsn)
    )

    # 下游自己的业务连接；SQLAlchemy 的 Connection / Session 也可以传给 finish_in
    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE TEMP TABLE jobs (id text PRIMARY KEY, status text)")
        conn.execute("INSERT INTO jobs VALUES ('job-tx-example', 'running')")
        conn.commit()
        try:
            with ao.job("job-tx-example") as job:
                # …各阶段…
                conn.execute("UPDATE jobs SET status = 'done' WHERE id = 'job-tx-example'")
                msg_id = job.finish_in(conn)  # 发送进入同一个事务；异常会抛给调用方
                if fail:
                    raise RuntimeError("business failure after finish_in")
            conn.commit()
            print(f"committed msg_id={msg_id} trace_id={job.trace_id}")
        except RuntimeError as e:
            conn.rollback()
            print(f"rolled back: {e}")
    ao.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
