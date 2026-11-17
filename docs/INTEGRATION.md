# 接入下游项目

下游用 `core`（可选 `metrics`）profile 部署 agentobs，业务 schema 由下游自己的迁移工具管理，可与 obs 共用一个 Postgres 实例。

本文每段代码都摘自 `python/examples/` 下的可运行脚本，脚本全部用 mock 模型，不需要 API key。`tests/test_examples.py` 会运行这些脚本，并检查本文的代码块与脚本逐字一致。

```bash
cd python
uv sync
uv run python -m examples.run_all      # 或 make examples
```

## 1. 安装 SDK

在下游项目里安装（`agentobs` 包本身；需要 RabbitMQ 时 pika 已包含在依赖里）：

```bash
pip install "agentobs[langchain] @ git+https://github.com/Hana1025/agentobs#subdirectory=python"
```

## 2. 初始化

完整脚本：`python/examples/quickstart.py`，运行 `python -m examples.quickstart`。

<!-- example: python/examples/quickstart.py -->
```python
ao.init(
    service_name="downstream-pipeline",
    project_id="downstream",
    otlp_endpoint=os.environ.get("AGENTOBS_OTLP_ENDPOINT"),  # 如 http://otel-collector:4318
    metrics_port=None,  # 设成 9464 即开放 /metrics
    capture_content=False,  # 默认不采集正文
    queue=ao.queue_from_url(queue_url) if queue_url else None,  # postgresql:// 或 amqp://
    # 也可以传 pricing.yaml 的路径
    pricing={"models": {"vendor/model-x": {"input": 1.0, "output": 4.0}}},
)
```

- `queue_from_url` 按连接串选择队列：`postgresql://…` 用 pgmq，`amqp://…` 用 RabbitMQ。只改配置即可切换。
- 底层模型客户端必须关掉内置重试，比如 `OpenAI(max_retries=0)`，否则超时会叠加。

## 3. 包一次模型调用，上报阶段 metric

<!-- example: python/examples/quickstart.py -->
```python
with ao.job("job-0001", attributes={"pages": 3}) as job:
    with ao.stage("transcribe") as st:
        r = ao.llm_call(
            call=lambda timeout: client.chat.completions.create(
                model="vendor/model-x", messages=messages, timeout=timeout
            ),
            provider="openrouter",
            model="vendor/model-x",
            attempt_timeout_s=60,
            deadline_s=300,
            max_attempts=3,
            parse=ao.parsers.json_object,
        )
        st.set_metric("pages_with_text", r.output["pages_with_text"])
```

- `r.output` 是 parse 后的结果；`r.input_tokens`、`r.output_tokens`、`r.cost_usd`、`r.attempts` 可用于业务日志。
- `st.set_metric(name, number)` 写成 span 属性 `agentobs.metric.<name>`。分诊只看这些数值，要让分诊看到什么就在这里上报什么。根 span 的 metric 用 `job.set_metric`，或在 `ao.job(..., attributes=...)` 里传数值。

## 4. 自定义分诊规则

在 worker 的 `triage.yaml` 里加声明式规则，只支持比较，不支持任意表达式。完整脚本：`python/examples/custom_rule.py`。

<!-- example: python/examples/custom_rule.py -->
```yaml
rules:
  - id: missing_input_pages
    hard: false
    left:  {stage: structure, metric: pages_mapped}
    op: "<"
    right: {stage: job, metric: pages}
```

`stage: job` 表示根 span 上的 metric；`right` 也可以写常量 `{value: 1}`。改了配置后 `triage_version` 会变，新旧版本的分诊结果并存。

## 5. 事务模式发事件（只支持 pgmq）

把 `job_finished` 的发送放进业务事务，与业务状态更新同时提交或回滚。完整脚本：`python/examples/transactional_pgmq.py`，加 `--fail` 可看到回滚后队列里没有消息。

<!-- example: python/examples/transactional_pgmq.py -->
```python
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
```

- 不调用 `finish_in` 时是默认模式：SDK 用自己的连接发送，失败只记日志和 `agentobs_queue_send_failures_total`，不影响业务。
- RabbitMQ 下调用 `finish_in` 会抛 `NotImplementedError`：AMQP 消息无法加入 Postgres 事务。

## 6. LangChain 链

回调处理器让链里的模型调用产生和 `llm_call` 同样属性的 span，挂在当前 `ao.stage` 下。完整脚本：`python/examples/langchain_chain.py`。

<!-- example: python/examples/langchain_chain.py -->
```python
chain = (
    ChatPromptTemplate.from_messages([("user", "Summarise: {text}")])
    | model
    | StrOutputParser()
)
handler = AgentobsCallbackHandler(provider="openrouter", model="vendor/model-x")

with ao.job("job-lc-0001"), ao.stage("summarise"):
    out = chain.invoke({"text": "synthetic input"}, config={"callbacks": [handler]})
```

- 用 `Runnable.with_retry()` 重试时，每次尝试一个 span，`agentobs.attempt` 依次为 1、2、3。
- 模型客户端内部的重试对回调不可见，仍要关掉底层重试。

## 7. 切换到 RabbitMQ

1. 在 `deploy/.env` 设置 `RABBITMQ_ADMIN_PASSWORD`、`AGENTOBS_PIPELINE_MQ_PASSWORD`、`AGENTOBS_TRIAGE_MQ_PASSWORD`，并取消注释两个 `*_QUEUE_URL`。
2. 启动时加上 `--profile rabbitmq`（`make demo-rabbitmq`）。`rabbitmq-init` 会创建队列和账号。
3. 下游 pipeline 把 `AGENTOBS_QUEUE_URL` 改成 `amqp://agentobs_pipeline:…@rabbitmq:5672/%2F`。

拓扑：

| 队列 | 作用 |
| --- | --- |
| `job_finished` | 主队列；reject 后由原生 DLX 进 `job_finished_dlq` |
| `job_finished.retry` | 暂缓处理的消息带着读取次数改投到这里，按消息 TTL 到期后回到主队列 |
| `job_finished_dlq` | 死信队列；原因在 worker 日志和 `agentobs_triage_dlq_total{reason}` 里 |

## 8. 同时导出到 Langfuse

不自建 Langfuse（v3 需要 ClickHouse、Redis、对象存储，超出本项目的基础设施约束），只把 trace 额外导出到 Langfuse Cloud 或下游已有的实例：

1. 在 `deploy/.env` 设置 `LANGFUSE_OTLP_ENDPOINT`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`。
2. 用 `make demo-langfuse` 启动，等价于叠加 `deploy/compose.langfuse.yaml`。

collector 会多一条 pipeline：先删正文属性，再把 `agentobs.cost_usd` 复制成 Langfuse 认的 `gen_ai.usage.cost`，然后发出。

## 9. 看板鉴权

看板不做登录。在上游反代层鉴权并设置 `X-Forwarded-User`，nginx 会原样透传给 server 作为复核人。
