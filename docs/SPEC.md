# agentobs 实施规格（交给 Claude Code）

Nov 17, 2026 · @Huizhong

这份规格描述一个开源的 LLM agent 可观测与分层归因组件（暂名 agentobs）的 v1 实现，按第 11 节的里程碑顺序实施；设计理由见 [HSCVault 可观测性升级草案](https://claude.ai/artifact/AmYmogSgXuuJUj3MTnhKxW)。

## 0. 给 Claude Code 的工作方式和硬性约束

先读完全文，再按第 11 节的里程碑顺序做，每个里程碑结束时测试全绿、可以单独演示。

**工作方式**

1. 开始前把本规格保存为仓库根目录的 `docs/SPEC.md`，并在 `CLAUDE.md` 里写明“以 docs/SPEC.md 为准”。
2. 一次只做一个里程碑；做完更新 `docs/PROGRESS.md`（完成项、偏离规格的地方和原因、遗留问题）。
3. 规格没写到的细节，选最简单、可替换的做法，并记入 PROGRESS.md；与规格冲突时停下来问。
4. 小步提交，commit message 用 Conventional Commits（feat/fix/chore/test/docs）。

**硬性约束**

- **不加规格外的基础设施组件**（Redis、Kafka、ClickHouse、Elasticsearch 等）。需要时先问。
- **仓库里不出现任何真实业务数据**：不写入真实试卷、学生信息、业务 prompt、rubric、客户名。代码、注释、测试和样例里只用合成数据，业务项目名只写成通用的“下游项目”。
- **密钥只走环境变量**；提供 `.env.example`，`.env` 进 `.gitignore`。
- **默认不采集正文**：SDK 的 `capture_content` 默认 False；开启时先脱敏再发出。
- **不依赖外部 API 也能跑**：所有模型调用都有 mock 实现，CI 和 `make demo` 不需要任何 API key。
- **观测不能拖垮业务**：SDK 里的埋点、导出、发事件任何异常都只记日志，不抛给调用方（发事件与业务同事务时除外，见 5.4）。
- **每个模块有测试**：Python pytest、Java JUnit + Testcontainers、前端 Vitest；新代码至少覆盖主路径和一个失败路径。

## 1. 背景与 v1 范围

agentobs 给多阶段、多模型的 LLM 流水线提供三件事：全链路 trace 和指标、自动分诊归因、人工复核并沉淀成评测集。它是通用开源组件，第一个下游是一个 AI 阅卷项目，通过配置接入。

**要解决的故障类型**（demo 的故障注入也覆盖这些）：

- 模型供应商中途失败，任务卡在 pending，没有自动重试
- 模型输出的转义字符污染 JSON，静默损坏下游数据
- 输入里的草稿页被当成答题页，或答题页被跳过，结果偏低但没有报错
- SDK 默认超时叠加多层重试，单个任务耗时失控
- token 成本和延迟没有聚合视角

**分层归因思路：** 第零层确定性规则 → System 1 便宜模型对每条 trace 的压缩状态打标签并给概率 → 只有可疑的升级到 System 2 推理模型读完整 trace 做根因 → 低置信度进人工复核 → 人工结论回流成标注集。

**v1 范围**

- Python 埋点 SDK（OpenTelemetry span + Prometheus 指标 + 任务完成事件）
- Java 观测后端（OTLP 入库、查询与复核 API）
- Python 分诊 worker（规则、System 1 的小 LLM 后端、System 2）
- React 看板（总览、trace 列表、trace 详情、复核队列、评测）
- 合成 demo 流水线 + 故障注入 + 评测脚本 + CI 回归门
- Docker Compose 一键部署，单机 4 核 8 GB 可运行

**非目标（v1 不做）**

- LLM 代理网关：观测不进模型调用热路径
- 多租户、账号系统、权限管理（看板靠反代层鉴权）
- 实时推送（WebSocket/SSE）、采样、高可用
- prompt 管理、在线 prompt 实验
- System One 类专用模型后端：只留接口和配置位，不实现（见 6.3）

## 2. 架构与数据流

Python 产生数据并做分诊，Java 是观测表唯一的写入方和查询入口，React 只调 Java API。架构图见草案文档第 9 节。

| 服务 | 语言 | 职责 |
| --- | --- | --- |
| pipeline（下游或 demo） | Python | 业务流水线，引入 agentobs SDK |
| otel-collector | — | 接收 OTLP、批量、脱敏兜底、转发给 server |
| server | Java | OTLP 入库；查询、分诊写入、复核 API；暴露自身指标 |
| triage-worker | Python | 消费任务完成事件，跑规则 / System 1 / System 2 |
| postgres | — | obs schema、pgmq 队列；下游业务 schema 可共用同一实例 |
| web | TypeScript | React 看板，由 nginx 提供静态文件并反代 /api |
| prometheus / alertmanager / grafana | — | 聚合指标、第零层告警、运维看板 |

**数据流**

1. pipeline 的每次模型调用经过 SDK 封装，产生 span；每个阶段一个父 span，整个任务一个根 span。
2. SDK 用 OTLP/HTTP 把 span 发给 otel-collector，collector 批量后用 `otlphttp` 导出到 server 的 `/v1/traces`。
3. server 把 span 写入 `obs.spans`，按 (trace\_id, span\_id) 去重。
4. 任务结束（成功或失败）时，SDK 往 pgmq 队列 `job_finished` 发一条只含 ID 的消息。
5. triage-worker 读消息 → 通过 server 内部 API 拉完整 trace → 规则 → 状态提取 → System 1 → 路由 → 必要时 System 2 → 结果 POST 回 server → 归档消息。
6. web 轮询 server 的查询 API；人工复核结果经 server 写入并生成评测用例。
7. prometheus 抓取 pipeline、triage-worker、server、collector 的指标；grafana 以 prometheus 为数据源。

**时序陷阱（必须处理）：** 第 4 步的事件可能早于第 2–3 步的 span 入库（collector 有批量延迟）。worker 拉到的 trace 如果还没有根 span 或根 span 未结束，就不归档消息、让可见性超时过后重试；超过最大次数按死信处理（见 6.1）。

## 3. 仓库结构与技术栈

一个 monorepo，三种语言各占一个顶层目录，部署配置集中在 `deploy/`。

```text
agentobs/
├── CLAUDE.md
├── Makefile                  # make dev / demo / test / eval / lint
├── docs/                     # SPEC.md, PROGRESS.md, ADR
├── python/
│   ├── pyproject.toml        # uv workspace
│   ├── agentobs/             # 可发布的 SDK 包
│   │   ├── core/             # tracer、llm_call 封装、脱敏、指标、事件
│   │   ├── queue/            # QueuePort + pgmq 实现（rabbitmq 实现在 M6）
│   │   └── adapters/         # LangChain 回调（M6，可选）
│   ├── triage/               # 分诊 worker
│   │   ├── rules.py  state.py  router.py  worker.py
│   │   └── backends/         # rules_backend, llm_json, systemone_http（桩）
│   ├── demo/                 # 合成流水线 + 故障注入
│   ├── eval/                 # 评测脚本与指标
│   └── tests/
├── server/                   # Java Spring Boot（Gradle Kotlin DSL）
│   └── src/main/java/dev/agentobs/{ingest,query,triage,review,config}
├── web/                      # React + Vite
└── deploy/
    ├── compose.yaml
    ├── .env.example
    ├── postgres/             # Dockerfile（带 pgmq）、init SQL
    ├── otel-collector/config.yaml
    ├── prometheus/{prometheus.yml,alerts.yml}
    ├── alertmanager/
    ├── grafana/{provisioning,dashboards}
    └── nginx/default.conf
```

| 层 | 选择 |
| --- | --- |
| Python | 3.12，uv 管依赖，ruff + pytest；pydantic v2、httpx、opentelemetry-sdk、opentelemetry-exporter-otlp-proto-http、prometheus-client、pgmq（官方 Python 客户端）、openai SDK（访问 OpenRouter 等兼容接口） |
| Java | Java 21，Spring Boot 当前稳定版；web、jdbc、actuator、validation、Micrometer Prometheus registry、Flyway（含 flyway-database-postgresql）、springdoc-openapi、opentelemetry-proto（解析 OTLP protobuf）、Testcontainers |
| 前端 | Node 当前 LTS，React + TypeScript + Vite、React Router、TanStack Query、TanStack Table、ECharts、shadcn/ui + Tailwind、openapi-typescript、Vitest |
| 基础设施 | PostgreSQL 17 + pgmq 扩展、otel/opentelemetry-collector-contrib、Prometheus、Alertmanager、Grafana、nginx |

版本写死在锁文件和镜像 tag 里，不用 `latest`。

## 4. 数据模型

观测表放在 `obs` schema，由 server 的 Flyway 迁移管理（`spring.flyway.schemas=obs`）；队列在 `pgmq` schema，由 `deploy/postgres` 的初始化 SQL 创建。下面是起点，字段可以增加，但保持列名和语义。

### 4.1 obs schema

```sql
CREATE TABLE obs.spans (
  trace_id        char(32)    NOT NULL,
  span_id         char(16)    NOT NULL,
  parent_span_id  char(16),
  project_id      text        NOT NULL,
  service_name    text        NOT NULL,
  name            text        NOT NULL,
  kind            text,
  start_time      timestamptz NOT NULL,
  end_time        timestamptz NOT NULL,
  duration_ms     double precision GENERATED ALWAYS AS
                    (EXTRACT(EPOCH FROM (end_time - start_time)) * 1000) STORED,
  status_code     text        NOT NULL,          -- UNSET | OK | ERROR
  status_message  text,
  -- 从 attributes 提升出来、用于筛选和聚合的列（见 5.2）
  job_id          text,
  stage           text,
  model           text,
  input_tokens    integer,
  output_tokens   integer,
  cost_usd        numeric(12,6),
  attempt         integer,
  attributes      jsonb       NOT NULL DEFAULT '{}',
  events          jsonb       NOT NULL DEFAULT '[]',
  ingested_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX ON obs.spans (project_id, start_time DESC);
CREATE INDEX ON obs.spans (job_id);
CREATE INDEX ON obs.spans (trace_id) WHERE parent_span_id IS NULL;

-- 每条 trace 一行；根 span = parent_span_id IS NULL
CREATE VIEW obs.trace_summary AS
SELECT s.trace_id,
       max(s.project_id)                                   AS project_id,
       max(s.job_id)                                       AS job_id,
       min(s.start_time)                                   AS start_time,
       max(s.end_time)                                     AS end_time,
       bool_or(s.parent_span_id IS NULL)                   AS has_root,
       max(s.status_code) FILTER (WHERE s.parent_span_id IS NULL) AS root_status,
       count(*)                                            AS span_count,
       count(*) FILTER (WHERE s.status_code = 'ERROR')     AS error_count,
       sum(s.input_tokens)                                 AS input_tokens,
       sum(s.output_tokens)                                AS output_tokens,
       sum(s.cost_usd)                                     AS cost_usd,
       max(s.attempt)                                      AS max_attempt
FROM obs.spans s
GROUP BY s.trace_id;

CREATE TABLE obs.trace_triage (
  id               bigserial PRIMARY KEY,
  trace_id         char(32)  NOT NULL,
  project_id       text      NOT NULL,
  job_id           text,
  triage_version   text      NOT NULL,          -- 规则+后端+阈值配置的版本号
  rules_fired      text[]    NOT NULL DEFAULT '{}',
  state            jsonb     NOT NULL,          -- 状态提取器输出，不含正文
  s1_backend       text,
  s1_answers       jsonb,                       -- 每个问题：answer, probs, confidence
  s1_label         text,                        -- normal | suspicious | failed
  s1_confidence    double precision,
  route            text      NOT NULL,          -- pass | alert | escalate
  s2_model         text,
  s2_root_cause    text,                        -- 见 6.4 的枚举
  s2_confidence    double precision,
  s2_evidence      jsonb,                       -- [{span_id, attribute, note}]
  s2_summary       text,
  review_status    text      NOT NULL DEFAULT 'none',  -- none | pending | reviewed
  triage_cost_usd  numeric(12,6) NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (trace_id, triage_version)
);
CREATE INDEX ON obs.trace_triage (project_id, created_at DESC);
CREATE INDEX ON obs.trace_triage (review_status) WHERE review_status = 'pending';

CREATE TABLE obs.reviews (
  id               bigserial PRIMARY KEY,
  triage_id        bigint NOT NULL REFERENCES obs.trace_triage(id),
  reviewer         text   NOT NULL,
  human_label      text   NOT NULL,
  human_root_cause text,
  note             text,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE obs.eval_cases (
  id                  bigserial PRIMARY KEY,
  trace_id            char(32),
  source              text  NOT NULL,          -- injected | human_review
  fault_type          text,                    -- 见 10.2；正常样本为 'none'
  expected_label      text  NOT NULL,
  expected_stage      text,
  expected_root_cause text,
  state_snapshot      jsonb NOT NULL,          -- 重跑评测不依赖原 trace
  created_at          timestamptz NOT NULL DEFAULT now()
);
```

- 入库用 `INSERT … ON CONFLICT (trace_id, span_id) DO NOTHING`：OTel span 结束后不可变，重复只会来自导出重试。不做 UPDATE。
- `trace_summary` 先用普通视图；列表查询变慢时再改成入库时 upsert 的汇总表。
- 数据保留：定时任务删除 90 天前的 spans（天数可配），triage、reviews、eval\_cases 不删。

### 4.2 pgmq 队列

```sql
CREATE EXTENSION IF NOT EXISTS pgmq;
SELECT pgmq.create('job_finished');
SELECT pgmq.create('job_finished_dlq');
```

消息体（JSON，带版本号）：

```json
{"v": 1, "project_id": "demo", "job_id": "job-0042", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "status": "succeeded", "finished_at": "2026-11-17T01:23:45Z"}
```

### 4.3 数据库账号

| 账号 | 权限 |
| --- | --- |
| agentobs\_server | obs schema 的所有权（跑 Flyway） |
| agentobs\_pipeline | 对 `job_finished` 队列 send；不能访问 obs |
| agentobs\_triage | 对两个队列 read / archive / send；不能访问 obs（数据都走 server API） |

## 5. Python 埋点 SDK（`python/agentobs`）

SDK 是下游接入的唯一入口：三个上下文管理器加一个模型调用封装，下游不直接碰 OpenTelemetry。

### 5.1 接口

```python
import agentobs as ao

ao.init(
    service_name="demo-pipeline",
    project_id="demo",
    otlp_endpoint="http://otel-collector:4318",
    metrics_port=9464,                 # None = 不开 /metrics
    capture_content=False,             # 默认不采集正文
    redactor=ao.DefaultRedactor(),     # 可替换
    queue=ao.PgmqQueue(dsn=...),       # None = 不发事件
    pricing="pricing.yaml",            # model -> USD / 1M input/output tokens
)

with ao.job("job-0042", attributes={"pages": 12}) as job:      # 根 span
    with ao.stage("transcribe") as st:                         # 阶段 span
        out = ao.llm_call(
            call=lambda timeout: client.chat.completions.create(..., timeout=timeout),
            provider="openrouter", model="vendor/model-x",
            attempt_timeout_s=60, deadline_s=300, max_attempts=3,
            parse=ao.parsers.json_object,          # 可选；解析失败算可重试错误
        )
        st.set_metric("pages_with_text", 11)       # 业务无关的数值，供分诊使用
```

- `job` 退出时：根 span 设状态（有异常 = ERROR 并重新抛出），然后发 `job_finished` 消息。
- `st.set_metric(name, number)` 写成 span 属性 `agentobs.metric.<name>`。分诊状态提取器只依赖这些通用数值，所以下游要让分诊看到什么，就在这里上报什么。
- 也提供 async 版本（`ao.llm_call_async`）。

### 5.2 span 属性

标准属性按 OpenTelemetry GenAI 语义约定（该规范仍处于 Development 状态，实现时核对当前版本的属性名并在一个常量文件里集中定义）。模型调用 span 名为 `chat {model}`。

| 属性 | 位置 | server 提升到列 |
| --- | --- | --- |
| `service.name`、`agentobs.project_id` | resource | service\_name、project\_id |
| `agentobs.job.id` | 所有 span | job\_id |
| `agentobs.stage` | 阶段及其子 span | stage |
| `gen_ai.operation.name`、`gen_ai.provider.name`、`gen_ai.request.model`、`gen_ai.response.model` | 模型调用 span | model 取 request.model |
| `gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens` | 模型调用 span | input\_tokens、output\_tokens |
| `gen_ai.response.finish_reasons` | 模型调用 span | — |
| `agentobs.attempt`、`agentobs.retry_reason` | 模型调用 span | attempt |
| `agentobs.cost_usd` | 模型调用 span | cost\_usd |
| `agentobs.output.chars`、`agentobs.output.control_chars`、`agentobs.output.parse_ok` | 模型调用 span（SDK 自动算，不含正文） | — |
| `agentobs.metric.*` | 任意 span | — |
| `agentobs.content.input` / `agentobs.content.output` | 仅 capture\_content=True，先脱敏、截断到 8k 字符 | — |

每次尝试是一个独立的模型调用 span（attempt = 1, 2, 3…），失败的尝试 status=ERROR 并记 `agentobs.retry_reason`（timeout | rate\_limited | server\_error | parse\_error | connection）。

### 5.3 Prometheus 指标

| 指标 | 类型 | 标签 |
| --- | --- | --- |
| `agentobs_llm_requests_total` | counter | stage, model, status |
| `agentobs_llm_latency_seconds` | histogram | stage, model |
| `agentobs_llm_tokens_total` | counter | stage, model, direction |
| `agentobs_llm_cost_usd_total` | counter | stage, model |
| `agentobs_llm_retries_total` | counter | stage, model, reason |
| `agentobs_output_parse_failures_total` | counter | stage |
| `agentobs_jobs_total` | counter | status |
| `agentobs_job_duration_seconds` | histogram | status |
| `agentobs_jobs_in_progress` | gauge | — |
| `agentobs_job_oldest_in_progress_seconds` | gauge | — |
| `agentobs_queue_send_failures_total` | counter | queue |

标签里绝不放 job\_id、trace\_id 这类高基数值。

### 5.4 发事件

- 默认模式：SDK 用自己的连接发送；失败只记日志和 `agentobs_queue_send_failures_total`，不影响业务。
- 事务模式：`job.finish_in(conn)` 把发送放进调用方传入的数据库连接或 SQLAlchemy session 的事务里，和业务状态更新同时提交或回滚；这种模式下发送异常会抛给调用方。
- 队列通过 `QueuePort` 接口访问（`send(queue, msg, conn=None)`、`read(queue, vt_s, qty)`、`archive`、`delete`），v1 只实现 pgmq。

### 5.5 重试与超时

- 只有一层重试：底层客户端的内置重试必须关掉（openai SDK 设 `max_retries=0`），否则超时会叠加。
- 每次尝试用 `min(attempt_timeout_s, 剩余 deadline)` 作为超时；超过 `deadline_s` 不再重试。
- 重试：超时、429、5xx、连接错误、解析失败；不重试：其他 4xx。退避 2s 起翻倍加随机抖动，有 Retry-After 时按它。
- 成本：优先用供应商返回的费用，没有就按 pricing.yaml 算；都没有则不写 cost 属性，不猜。

## 6. Python 分诊 worker（`python/triage`）

worker 无状态、可以起多个，只通过队列和 server API 与外界交互，不直连 obs 表。所有阈值、规则、后端选择都在 `triage.yaml`；配置和后端版本的哈希就是 `triage_version`。

### 6.1 主循环

1. `read('job_finished', vt=60s, qty=10)`。
2. 对每条消息 `GET /api/internal/traces/{trace_id}`。
3. trace 不存在、没有根 span、或处理抛异常：不归档，等可见性超时后重读。消息 `read_ct` 达到 `max_reads`（默认 10）时，把原消息加上 `reason` 发到 `job_finished_dlq` 再归档原消息。
4. 规则 → 状态提取 → System 1 → 路由 →（升级时）System 2。
5. `POST /api/internal/triage`；返回 2xx 或「同一 trace\_id + triage\_version 已存在」都算成功，然后归档消息。
6. 暴露指标：`agentobs_triage_total{route,s1_label,backend}`、`agentobs_triage_confidence`（histogram）、`agentobs_triage_cost_usd_total{system}`、`agentobs_triage_latency_seconds{system}`、`agentobs_triage_dlq_total{reason}`。

### 6.2 状态提取器（`state.py`）

把一条 trace 压缩成不超过约 2k token 的 `TraceState`（pydantic），只含数值和枚举，不含正文：

- job：status、duration\_s、span\_count、error\_count、cost\_usd、tokens、max\_attempt，以及 job 根 span 上的 `agentobs.metric.*`
- 每个 stage：status、duration\_s、llm\_calls、retries（按 reason 计数）、parse\_failures、control\_chars、output\_chars、该阶段的 `agentobs.metric.*`
- 相对基线：每个 stage 的 duration\_ratio、cost\_ratio（除以该项目最近 200 条成功 trace 的中位数，来自 `GET /api/internal/baselines`；样本不足 20 条时为 null）
- rules\_fired

### 6.3 规则与 Decider

内置规则（阈值可配）：job\_failed、retries\_high（任一阶段 ≥ 2 次）、parse\_failure、control\_chars\_present、slow\_stage（duration\_ratio > 3）、cost\_spike（cost\_ratio > 3）。下游用声明式规则比较自己上报的 metric，不支持任意表达式：

```yaml
rules:
  - id: missing_input_pages
    hard: false
    left:  {stage: structure, metric: pages_mapped}
    op: "<"
    right: {stage: job, metric: pages}
```

Decider 接口：

```python
class Question(BaseModel):
    id: str
    kind: Literal["choice", "bool", "score"]
    prompt: str
    options: list[str] | None = None     # choice
    score_range: tuple[int, int] | None = None

class Answer(BaseModel):
    question_id: str
    answer: str
    probs: dict[str, float]              # 各选项概率，和为 1
    confidence: float                    # = max(probs)

class Decider(Protocol):
    name: str
    version: str
    def decide(self, state: TraceState, questions: list[Question]) -> list[Answer]: ...
```

默认问题：health（normal / suspicious / failed）、suspect\_stage（各 stage 名 + infrastructure + none）、missing\_input（bool）、severity（0–3）。

| 后端 | v1 | 说明 |
| --- | --- | --- |
| `rules` | 实现 | 基线；把 rules\_fired 映射成固定概率的答案，始终可用 |
| `llm_json` | 实现 | OpenAI 兼容接口 + JSON schema 输出。选项编码成单 token 字母（A/B/C…），有 logprobs 时从它算概率；没有就采样 k=5（temperature 0.7）按投票频率算。不使用模型口头报告的置信度。提供 mock 实现 |
| `systemone_http` | 桩 | 只有类和配置位，调用时抛 NotImplementedError；协议字段未核实，不要臆造 |

### 6.4 路由与 System 2

```yaml
routing:
  pass_if:     {p_normal_gte: 0.90, no_rules_fired: true}
  escalate_if: {p_failed_gte: 0.50, or_hard_rule: true, or_p_suspicious_gte: 0.40}
  otherwise: alert
  s2_budget_per_hour: 30        # 超预算时 escalate 降级为 alert
review:
  if_s2_confidence_lt: 0.70
  or_severity_gte: 2
```

以上阈值是占位，等第 10 节评测后再定。

System 2 输入：`TraceState` + 完整 span 列表（属性已脱敏；只有下游开了 capture\_content 且配置允许时才带正文）。输出 JSON：

```json
{"root_cause": "missing_or_misclassified_input", "confidence": 0.82,
 "evidence": [{"span_id": "00f067aa0ba902b7", "attribute": "agentobs.metric.pages_mapped", "note": "映射页数少于输入页数"}],
 "summary": "一句话结论"}
```

- root\_cause 枚举：input\_extraction\_error、missing\_or\_misclassified\_input、output\_corruption、scoring\_anomaly、model\_disagreement、provider\_failure、timeout\_cascade、other。
- evidence 里的 span\_id 必须在这条 trace 里存在，不存在的丢弃并把 confidence 压到 0.5 以下。
- JSON 解析失败重试一次，仍失败则 root\_cause=other、进人工复核。

## 7. Java 观测后端（`server/`）

server 提供三类接口：OTLP 入库（只对 collector）、内部接口（只对 triage-worker 和 eval 脚本）、公开查询与复核接口（对 web）。三类分在不同包，内部和入库接口不经 nginx 对外暴露。

### 7.1 接口

| 方法与路径 | 调用方 | 说明 |
| --- | --- | --- |
| `POST /v1/traces` | collector | OTLP/HTTP，`application/x-protobuf`（可选支持 JSON）。成功返回 200 + 空的 ExportTraceServiceResponse；请求体损坏 400；数据库不可用 503（collector 会重试） |
| `GET /api/internal/traces/{traceId}` | worker | trace\_summary + 全部 span；不存在 404 |
| `GET /api/internal/baselines?project_id=&window=200` | worker | 每个 stage 的中位耗时和成本、样本数 |
| `POST /api/internal/triage` | worker | 写 trace\_triage；同 trace\_id + triage\_version 重复返回 409 |
| `POST /api/internal/eval-runs` | eval 脚本 | 写评测结果（新表 `obs.eval_runs`：id、backend、triage\_version、dataset\_size、metrics jsonb、created\_at） |
| `GET /api/traces` | web | 参数 project\_id、from、to（必填，跨度 ≤ 31 天，否则 400）、status、s1\_label、route、conf\_min、conf\_max、stage、review\_status、sort、page、size（≤ 100） |
| `GET /api/traces/{traceId}` | web | summary、按树顺序的 span、最新一条 triage、复核记录 |
| `GET /api/metrics/summary?project_id&from&to` | web | 成功率、P95 job 耗时、平均单 job 成本、升级率、待复核数；用 SQL 的 percentile\_cont 计算 |
| `GET /api/reviews/queue?project_id&page` | web | review\_status = pending，按 severity、created\_at 排序 |
| `POST /api/reviews` | web | `{triage_id, human_label, human_root_cause?, note?}`；同一事务内写 reviews、把 review\_status 设为 reviewed、用 triage.state 生成一条 eval\_cases（source=human\_review） |
| `GET /api/eval/runs`、`GET /api/eval/runs/{id}` | web | 评测结果列表和详情 |
| POST /api/internal/eval-cases | eval 脚本 | 批量写入 injected 评测用例；同 trace\_id + source 已存在则跳过 |
| GET /api/me | web | 返回当前用户名（来自 X-Forwarded-User，没有时为 anonymous） |

### 7.2 实现要求

- **入库：** 用 opentelemetry-proto 解析；trace\_id/span\_id 转小写 hex；纳秒时间戳转 timestamptz；AnyValue 转 JSON；按 5.2 的表提升列；`JdbcTemplate.batchUpdate` + `ON CONFLICT DO NOTHING`。
- **查询：** trace 树和聚合用 JdbcTemplate 手写 SQL，不用 ORM 生成 join；所有列表查询先按 project\_id + 时间过滤。
- **鉴权：** 内部接口校验 `X-Agentobs-Token`（环境变量）；公开接口不自己做登录，复核人取反代设置的 `X-Forwarded-User`，没有时为 `anonymous`。
- **错误格式：** Spring 的 ProblemDetail（RFC 9457）。
- **文档：** springdoc 在 `/v3/api-docs` 输出 OpenAPI，前端类型从它生成；CI 里检查生成的类型与提交的一致。
- **指标：** Actuator + Micrometer 在 `/actuator/prometheus`；自定义 `agentobs_ingest_spans_total{result}`、`agentobs_ingest_batch_seconds`、`agentobs_review_backlog`（每 60 秒刷新）。
- **数据保留：** `@Scheduled` 每天删除超过 `AGENTOBS_RETENTION_DAYS`（默认 90）的 spans，分批删除。
- **测试：** Testcontainers 起 Postgres；入库测试用真实的 OTLP protobuf 样例，覆盖重复提交、缺少可选属性、超长属性。

## 8. 前端看板（`web/`）

看板只回答“哪条 trace 有问题、为什么、人工确认后进标注集”；聚合趋势和基础设施指标留给 Grafana，前端只放链接，不用 iframe。

| 路由 | 页面 | 内容 |
| --- | --- | --- |
| `/` | 总览 | 5 个指标卡（成功率、P95 耗时、平均单 job 成本、升级率、待复核数），时间范围选择，Grafana 链接 |
| `/traces` | trace 列表 | 筛选栏（时间必填、状态、S1 标签、路由、置信度区间、可疑阶段、复核状态），筛选条件同步到 URL query；默认按 severity 降序 |
| `/traces/:traceId` | trace 详情 | 左：瀑布图；右：规则命中、System 1（每个问题的答案和概率条、backend、triage\_version）、System 2（根因、置信度、摘要、证据列表）、复核记录 |
| `/review` | 复核队列 | 列表 + 展开式表单：确认或改标签、选根因、备注；提交后从队列移除并提示“已加入评测集” |
| `/eval` | 评测 | 各 eval run 对比表（召回、误报、ECE、单条成本），选中一个 run 显示可靠性曲线和覆盖率—准确率曲线（ECharts） |

**瀑布图组件（自己用 SVG 写，不引第三方 trace 组件）**

- 每行一个 span，按父子关系缩进，横向按 start\_time、duration 按比例画条；可以折叠子树。
- 颜色只表示状态：OK 中性色、ERROR 红色、重试尝试用虚线边框。
- 点击一行在侧栏显示属性（JSON 树）；点击 System 2 的一条证据，瀑布图滚动到并高亮对应 span。
- 500 个 span 以内不卡；超过时只渲染可见行。

**其他要求**

- 数据层：TanStack Query，列表和总览每 30 秒轮询、窗口失焦时停止；详情页不轮询。
- API 类型由 openapi-typescript 从 `/v3/api-docs` 生成，放 `web/src/api/schema.ts`，不手写接口类型。
- 正文属性（`agentobs.content.*`）默认折叠，点击后才显示；由构建时变量 `VITE_SHOW_CONTENT` 控制是否允许显示。
- 不做登录页；页面右上角显示后端返回的当前用户名。
- 测试：瀑布图的布局计算、筛选状态与 URL 同步、复核表单提交各有单测。

## 9. 部署（`deploy/`）

一份 `compose.yaml`，用 profiles 控制启动哪些服务；只有 nginx 和 grafana 映射到宿主机，其余只在内部网络。整套 core + metrics 要能在 4 核 8 GB 单机上跑。

### 9.1 服务与 profiles

| 服务 | profile | 内存上限 | 备注 |
| --- | --- | --- | --- |
| postgres | core | 2 GB | 自建镜像：官方 postgres:17 + pgmq（按 pgmq 官方 INSTALLATION 说明安装）；init SQL 建账号、扩展、队列；健康检查 pg\_isready |
| server | core | 768 MB | `-Xmx512m`；依赖 postgres 健康 |
| otel-collector | core | 256 MB | contrib 镜像，配置见 9.2 |
| triage-worker | core | 512 MB | 没配模型 API key 时自动用 mock 后端 |
| nginx | core | 64 MB | 提供 web 构建产物，反代 `/api/` 到 server；不转发 `/api/internal/` 和 `/v1/` |
| prometheus | metrics | 512 MB | 保留 15 天 |
| alertmanager | metrics | 128 MB | 默认接收器为空，预留 webhook / email 配置 |
| grafana | metrics | 256 MB | provisioning 自动加载数据源和面板 |
| demo-pipeline | demo | 1 GB | 合成流水线 + 故障注入（第 10 节） |
| rabbitmq | rabbitmq | 512 MB | M6 才做；队列切到 AMQP 实现 |

常用命令（Makefile 封装）：

- `make demo` = `docker compose --profile core --profile metrics --profile demo up -d`
- `make dev`：只起 postgres 和 collector，server / web / worker 本地跑
- `make test`、`make eval`、`make lint`

### 9.2 Collector 配置（起点）

```yaml
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 200
  attributes/redact:              # 默认删掉正文；demo 用另一份不删正文的配置
    actions:
      - {key: agentobs.content.input, action: delete}
      - {key: agentobs.content.output, action: delete}
  batch:
    timeout: 2s
    send_batch_size: 512
exporters:
  otlphttp/server:
    endpoint: http://server:8080   # 导出器会自动拼上 /v1/traces
    compression: none              # server 不处理 gzip 请求体；如果要开，server 需先支持
    headers:
      X-Agentobs-Token: ${env:AGENTOBS_INGEST_TOKEN}
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, attributes/redact, batch]
      exporters: [otlphttp/server]
```

实现时按所用 collector 版本核对配置键名；langfuse profile（可选）就是在这里多加一个导出器。

### 9.3 Prometheus 与第零层告警

抓取目标：pipeline `:9464`、triage-worker `:9465`、server `/actuator/prometheus`、otel-collector 自身指标。

| 告警 | 条件（阈值为占位） |
| --- | --- |
| JobStuck | `agentobs_job_oldest_in_progress_seconds > 900` 持续 5m |
| HighRetryRate | 重试数 / 请求数 > 0.2，10m 窗口 |
| OutputParseFailures | `increase(agentobs_output_parse_failures_total[15m]) > 0` |
| QueueSendFailures | `increase(agentobs_queue_send_failures_total[15m]) > 0` |
| TriageDeadLetter | `increase(agentobs_triage_dlq_total[15m]) > 0` |
| ReviewBacklog | `agentobs_review_backlog > 20` 持续 1h |
| IngestErrors | `agentobs_ingest_spans_total{result="error"}` 15m 内增长 |

Grafana 预置两个面板：LLM 调用（请求量、P95 延迟、token、成本、重试，按 stage/model）和分诊（路由分布、置信度分布、升级率、分诊成本、复核积压）。

### 9.4 环境变量（`.env.example`）

`POSTGRES_PASSWORD`、`AGENTOBS_SERVER_DB_PASSWORD`、`AGENTOBS_PIPELINE_DB_PASSWORD`、`AGENTOBS_TRIAGE_DB_PASSWORD`、`AGENTOBS_INGEST_TOKEN`、`AGENTOBS_INTERNAL_TOKEN`、`AGENTOBS_RETENTION_DAYS`、`LLM_BASE_URL`、`LLM_API_KEY`（空 = mock）、`S1_MODEL`、`S2_MODEL`、`GRAFANA_ADMIN_PASSWORD`。

### 9.5 接入下游项目

- 下游用 core（可选 metrics）profile，镜像固定版本；可以和下游共用一个 Postgres 实例，业务 schema 由下游自己的迁移工具管理。
- 提供 `docs/INTEGRATION.md`：安装 SDK、包一次模型调用、上报阶段 metric、写自定义规则、用事务模式发事件的最小示例。

## 10. Demo、故障注入与评测

demo 是一个和业务无关的合成流水线，结构上模仿“多页输入 → 抽取 → 结构化 → 双模型打分”；故障注入给每条 trace 带上标准答案，既用于演示，也是评测集和 CI 回归门的数据来源。

### 10.1 合成流水线（`python/demo`）

- 输入：由种子生成的假文档（N 页、每页一段假文本，其中部分标为答题页、部分为草稿页）。不含任何真实文本。
- 阶段：extract → structure → score（score 阶段调用两个模型并上报 `disagreement`）。
- 每个阶段用 `st.set_metric` 上报：pages、answer\_pages\_detected、pages\_mapped、score、disagreement 等。
- 模型调用默认走 mock（延迟、token、输出都由种子决定，可复现）；配了 `LLM_API_KEY` 时可切换到真实模型。
- 运行模式：`--rate 6/min` 持续产生流量（演示），或 `--batch 300 --seed 42` 一次性生成评测集。
- 默认混合比例：70% 正常，30% 均匀分布在各故障类型。

### 10.2 故障类型与标准答案

| fault\_type | 注入方式 | expected\_label | expected\_stage | expected\_root\_cause |
| --- | --- | --- | --- | --- |
| none | 不注入 | normal | none | — |
| transient\_rate\_limit | 第一次尝试返回 429，重试成功 | normal | none | —（检验误报） |
| provider\_failure | 某阶段所有尝试返回 503，job 失败 | failed | infrastructure | provider\_failure |
| timeout\_cascade | 尝试接近超时后失败，多次重试，总耗时超基线数倍 | suspicious | 注入的阶段 | timeout\_cascade |
| output\_corruption | 输出夹带控制字符或非法转义，部分可解析、部分不可解析 | suspicious | structure | output\_corruption |
| missing\_page | structure 的 pages\_mapped 少于输入页数 | suspicious | structure | missing\_or\_misclassified\_input |
| scratch\_as\_answer | extract 把草稿页当答题页、漏掉一个答题页，页数对得上但 score 偏低 | suspicious | extract | missing\_or\_misclassified\_input |
| model\_disagreement | 两个打分模型的分差超过阈值 | suspicious | score | model\_disagreement |
| score\_outlier | score 远低于该项目分布，其余一切正常 | suspicious | score | scoring\_anomaly |

注入器把标准答案写在根 span 的 `agentobs.eval.*` 属性上（只有 demo 用）。`make eval` 拉取这些 trace，用和 worker 同一个状态提取器生成状态，经 `POST /api/internal/eval-cases` 写入 `eval_cases`（source=injected）。scratch\_as\_answer 和 score\_outlier 是规则单独抓不到的类型，用来衡量 System 1 的增量价值。

### 10.3 评测（`python/eval`，`make eval`）

对同一批 eval\_cases 的 state\_snapshot，分别跑每个 Decider 后端和路由，输出：

- health 各类的精确率和召回；故障召回（非 normal 被判为 suspicious 或 failed）；正常样本误报率
- 按 fault\_type 拆开的召回（看出哪类故障漏得最多）
- ECE（10 个分箱）、可靠性曲线数据、覆盖率—准确率曲线数据
- 升级样本上 System 2 的 root\_cause 准确率
- 单条成本、p50/p95 延迟；以及“全部直接交给 System 2”的成本对照

结果写 `eval/reports/<timestamp>.json` 和一份 Markdown 摘要，并 POST 到 `/api/internal/eval-runs`。

### 10.4 CI 回归门

- CI 用固定种子生成数据集，只跑 rules 和 mock 版 llm\_json（确定性），不调用外部 API。
- 门槛（占位，写在 `eval/gates.yaml`）：故障召回 ≥ 0.90，正常样本误报率 ≤ 0.10。不达标则 CI 失败。
- 真实模型评测手动跑（`make eval BACKEND=llm_json LIVE=1`），不进 CI。

## 11. 里程碑与验收标准

按 M1 → M6 顺序做；每个里程碑的验收项全部勾上、CI 全绿才进入下一个。M1–M3 是最小可演示版本，M4–M5 是核心卖点，M6 可选。

### M1 骨架与部署底座

内容：仓库结构、Makefile、CLAUDE.md、compose core 骨架、带 pgmq 的 Postgres 镜像和 init SQL、server 空工程 + Flyway 迁移、web 空工程、CI（lint、测试、gitleaks）。

- [ ] `make demo` 后 core 服务全部 healthy
- [ ] Postgres 里 `pgmq.create` 成功，两个队列存在；三个数据库账号权限符合 4.3（有测试）
- [ ] Flyway 建出第 4 节的表和视图
- [ ] CI 在 PR 上跑 Python / Java / web 的 lint 和测试，以及密钥扫描

### M2 埋点到入库

内容：SDK（init、job、stage、llm\_call、重试与 deadline、指标、脱敏）、collector 配置、server 入库、demo 流水线（mock，先不注入故障）。

- [ ] demo 跑 20 个 job 后，obs.spans 有对应行，提升列填对，每条 trace 恰有一个根 span
- [ ] 同一批 span 重复提交不产生重复行
- [ ] capture\_content=False 时数据库里没有任何 `agentobs.content.*` 属性
- [ ] 重试测试：整体耗时不超过 deadline\_s + 1s；底层客户端重试确实关闭；不可重试的 4xx 不重试
- [ ] collector 或 server 宕机时 demo 流水线照常完成
- [ ] pipeline 的 `/metrics` 有 5.3 的指标，标签里没有 job\_id

### M3 查询 API、看板与指标

内容：7.1 的公开查询接口、OpenAPI 与生成的 TS 类型、web 的总览 / 列表 / 详情（含瀑布图）、nginx、metrics profile（Prometheus、告警规则、Grafana 面板）。

- [ ] `GET /api/traces` 缺时间范围或跨度 > 31 天返回 400
- [ ] 瀑布图层级和时间比例正确，重试尝试可辨认（有单测）
- [ ] 筛选条件写入 URL，刷新页面后保持
- [ ] Grafana 启动即有数据源和两个面板
- [ ] 让一个 demo job 挂住，JobStuck 告警在 Prometheus 里变为 firing
- [ ] nginx 不暴露 `/api/internal/` 和 `/v1/`

### M4 队列与 System 1

内容：QueuePort + pgmq、job\_finished 事件（默认模式和事务模式）、triage-worker、状态提取器、内置与声明式规则、rules 和 llm\_json（含 mock）后端、路由、内部 triage / baselines 接口、故障注入器、列表和详情页展示 S1 结果。

- [ ] 每个完成的 demo job 都有一条 trace\_triage
- [ ] 时序陷阱：人为延迟 collector 导出 2 分钟，worker 不产生空分诊，span 到达后正常处理
- [ ] 消息读取次数达到 max\_reads 后进入 dlq，指标和告警可见
- [ ] 事务模式：业务事务回滚时队列里没有消息（集成测试）
- [ ] 同一消息重复投递不产生重复 triage
- [ ] TraceState 序列化后不超过 2k token（用 tiktoken 或字符数估算），不含正文

### M5 System 2、复核与评测闭环

内容：System 2（含证据校验和预算）、复核队列页和 `POST /api/reviews`、eval 脚本和报告、eval-cases / eval-runs 接口、评测页、CI 回归门。

- [ ] 升级的 trace 都有 root\_cause，证据里的 span\_id 都真实存在
- [ ] 超过 s2\_budget\_per\_hour 后不再调用 System 2
- [ ] 复核提交后生成一条 eval\_case，并从队列消失
- [ ] `make eval` 产出 10.3 的全部指标，评测页能看到曲线
- [ ] 故意删掉一条内置规则时 CI 回归门失败

### M6 可选扩展

内容：RabbitMQ 的 QueuePort 实现和 profile、LangChain 回调适配器、langfuse profile（collector 多一个导出器）、`docs/INTEGRATION.md`。

- [ ] 只改配置把队列切到 RabbitMQ，M4 的队列集成测试全部通过（死信用 RabbitMQ 原生 DLX）
- [ ] 用 LangChain 写的示例链产生和 llm\_call 同样属性的 span
- [ ] INTEGRATION.md 的示例代码能直接运行

## 12. 默认值与待确认

下面的问题不阻塞实施：Claude Code 按“默认”列先做，结论出来后只改配置或局部代码。

| 问题 | 默认 |
| --- | --- |
| 项目正式名和 Java 包名 | agentobs，`dev.agentobs` |
| 开源许可证 | Apache-2.0 |
| System 1 / System 2 用哪个模型 | 通过 `S1_MODEL` / `S2_MODEL` 配置；代码里不写死模型名 |
| 路由、复核、CI 门槛的阈值 | 6.4 和 10.4 的占位值，等评测结果再调 |
| 看板登录 | 不做；下游在反代层鉴权，并设置 X-Forwarded-User |
| 下游服务器的云和区域、数据是否必须留在澳洲 | 不影响本仓库代码，只影响部署 |
| 下游业务 schema 的迁移工具 | 与 Flyway 各管各的 schema，Flyway 版本表放在 obs |
| System One 类模型后端的协议 | 只留桩，有官方文档后再实现 |
| 单机资源是否够用 | 按 9.1 的内存上限，M4 完成后用 `docker stats` 实测并记入 PROGRESS.md |
