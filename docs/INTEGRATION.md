# 接入下游项目

下游用 `core`（可选 `metrics`）profile 部署 agentobs，业务 schema 由下游自己的迁移工具管理，可与 obs 共用一个 Postgres 实例。

## 1. 安装 SDK

```bash
pip install "agentobs[pgmq] @ git+https://github.com/<org>/agentobs#subdirectory=python"
```

## 2. 初始化

```python
import os
import agentobs as ao

ao.init(
    service_name="downstream-pipeline",
    project_id="downstream",
    otlp_endpoint=os.environ["AGENTOBS_OTLP_ENDPOINT"],      # http://otel-collector:4318
    metrics_port=9464,
    capture_content=False,                                     # 默认不采集正文
    queue=ao.PgmqQueue(os.environ["AGENTOBS_QUEUE_DSN"]),     # agentobs_pipeline 账号
    pricing="pricing.yaml",
)
```

## 3. 包一次模型调用

```python
from openai import OpenAI

client = OpenAI(max_retries=0)   # 必须关掉底层重试，只保留 SDK 的一层

with ao.job(job_id, attributes={"pages": n_pages}) as job:
    with ao.stage("transcribe") as st:
        r = ao.llm_call(
            call=lambda timeout: client.chat.completions.create(
                model="vendor/model-x", messages=msgs, timeout=timeout),
            provider="openrouter", model="vendor/model-x",
            attempt_timeout_s=60, deadline_s=300, max_attempts=3,
            parse=ao.parsers.json_object,
        )
        st.set_metric("pages_with_text", r.output["pages_with_text"])
```

`r.output` 是 parse 后的结果，`r.input_tokens / output_tokens / cost_usd / attempts` 可用于业务日志。

## 4. 上报阶段 metric

`st.set_metric(name, number)` 写成 span 属性 `agentobs.metric.<name>`；分诊只看这些数值，
所以要让分诊看到什么就在这里上报什么（页数、映射页数、得分、分歧等）。根 span 的 metric 用 `job.set_metric`。

## 5. 自定义规则

在 worker 的 `triage.yaml` 里加声明式规则（只支持比较，不支持任意表达式）：

```yaml
rules:
  - id: missing_input_pages
    hard: false
    left:  {stage: structure, metric: pages_mapped}
    op: "<"
    right: {stage: job, metric: pages}
```

改了配置后 `triage_version` 会变，新的分诊结果与旧版本并存。

## 6. 事务模式发事件

把 `job_finished` 的发送放进业务事务：

```python
with ao.job(job_id) as job:
    ...  # 各阶段
    with engine.begin() as conn:           # SQLAlchemy；psycopg 连接也可以
        conn.execute(update_job_status_sql)
        job.finish_in(conn)                # 与业务更新同时提交或回滚；异常会抛给调用方
```

默认模式（不调用 `finish_in`）下 SDK 用自己的连接发送，失败只记日志和 `agentobs_queue_send_failures_total`。

## 7. 看板鉴权

看板不做登录；在上游反代层鉴权并设置 `X-Forwarded-User`，nginx 会原样透传给 server 作为复核人。
