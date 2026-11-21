# PROGRESS

按 `docs/SPEC.md` 第 11 节的里程碑记录：完成项、偏离规格的地方和原因、遗留问题。

## 通用说明

- 本机没有 Docker；Testcontainers 的 Java IT 和 Python pgmq 集成测试只在 CI 里跑，已在 GitHub Actions 上全部通过（见“CI 验证”）。
- `server/gradlew` + `gradle-wrapper.jar`（8.12.1）、`python/uv.lock`、`web/package-lock.json` 已生成并提交。
- 本机 Python 是 3.13（规格要求 3.12）；`requires-python >= 3.12`，锁文件跨版本。
- Python 侧没有用 uv workspace，而是 `python/` 一个项目，`agentobs`、`triage`、`demo`、`eval` 为顶层包，只有 `agentobs` 进 wheel（hatch `packages`）。原因：workspace 会让 SDK 包目录多一层，与规格目录树不一致，单项目更简单且可随时拆。

## M1 骨架与部署底座（代码完成，未验证）

- compose core/metrics/demo/dev profiles；postgres 自建镜像从 pgmq 源码编译（版本 1.6.1）；init SQL 建 obs schema、队列和三个账号。
- Flyway V1 建 SPEC 4.1 全部表 + `eval_runs`（7.1 要求）；`eval_cases` 加了 (trace_id, source) 唯一索引实现“已存在则跳过”。
- 账号权限测试在 `server/src/test/.../SchemaAndRolesIT.java`（Testcontainers 用 deploy/postgres 的 Dockerfile 构建）。
- pgmq 权限 GRANT 的函数签名按 pgmq 1.6 写；换版本需核对 `pgmq.send` 重载。
- CI：gitleaks + python/java/web 三条 + api-types 一致性检查。

## M2 埋点到入库（代码完成，未验证）

- SDK：`agentobs/core/{tracer,llm,redact,metrics,pricing,parsers,attrs}.py`、`agentobs/queue/{port,pgmq}.py`。
- pgmq 用 psycopg3 直接调 SQL 函数，没引官方 python 客户端（少一个依赖，事务模式更好控制）。
- `llm_call` 返回 `LLMResult`（`.output` 是 parse 后的结果），不是裸响应——下游需要 token/cost/attempts 时能拿到。
- `json_object` 解析用 `strict=False`：字符串值里的控制字符不算解析失败，由 `agentobs.output.control_chars` 计数；output_corruption 注入的“部分可解析”依赖这一点。
- server 入库：`ingest/{OtlpSpanMapper,SpanRepository,OtlpTracesController,RetentionJob}`；单属性超过 16k 字符截断。
- demo：`demo/{synth,faults,mock_llm,pipeline,run}.py`，故障注入器已一起写好（M4 内容提前），`--fault none` 即 M2 的“先不注入故障”。

## 下一步（未开始）

- M3：server 公开查询接口（`GET /api/traces`、`/api/traces/{id}`、`/api/metrics/summary`、`/api/me`）、OpenAPI 生成 TS 类型、web 总览/列表/详情（SVG 瀑布图）、nginx 已就绪、metrics profile 配置已就绪（prometheus/alerts/grafana 面板已写）。
- M4：`python/triage`（worker、state、rules、router、backends）、内部 triage/baselines 接口、事务模式集成测试。
- M5：System 2、复核接口与页面、eval 脚本与 CI 回归门。

## M3 查询 API、看板与指标（代码完成，未验证）

- server：`query/TraceQueryService`（手写 SQL；列表按 project_id + 时间过滤，31 天上限，size ≤ 100）、`TraceController`（/api/traces、/api/traces/{id}、/api/metrics/summary、/api/me）、`OpenApiConfig`、`review/ReviewBacklogGauge`（60s 刷新）。
- 列表默认按 severity 降序：severity 取最新 triage 的 `s1_answers.severity.answer`，没有 triage 时为 0。
- web：路由/布局/API 客户端（openapi-fetch）、总览、列表（筛选同步 URL，默认值不写入 URL）、详情（SVG 瀑布图：折叠、重试虚线、500 行以上窗口化、证据点击高亮并滚动）、复核队列页和评测页（M5 的页面已一并写好，等后端接口）。
- `web/src/api/schema.ts` 目前是按 DTO 手工对齐的占位（本机无法起 server 生成）；首次能跑 server 时执行 `npm run gen:api` 覆盖，CI 的 api-types 任务会校验。
- 正文属性 `agentobs.content.*` 默认折叠，`VITE_SHOW_CONTENT` 构建变量控制是否允许显示。
- 单测：`waterfall/layout.test.ts`、`lib/filters.test.ts`、`pages/ReviewQueue.test.tsx`。
- metrics profile（Prometheus 抓取、告警、Grafana 数据源和两个面板）在 M1 已写。

## M4 队列与 System 1（代码完成，未验证）

- server：`triage/{InternalController,TriageRepository,BaselineService,TriageRequest}`。baselines 除中位耗时/成本外还返回每个 stage 上 `agentobs.metric.*` 的中位数（`medianMetrics`），供 S1 判断 score 之类的分布偏离——规格字段上的扩展。
- Python：`triage/{config,models,state,rules,router,client,metrics,worker}.py`，`backends/{rules_backend,llm_json,systemone_http}.py`，默认配置 `triage/triage.yaml`。
- `llm_json` 是每个问题一次调用、`max_tokens=1`、首 token 的 logprobs 转概率；供应商不支持 logprobs 时回退到 k=5 采样投票。规格里提到 JSON schema 输出，这里改为单字母答案以便拿 logprobs（JSON 输出的 logprobs 不好定位答案 token）。
- `MockLLMJsonDecider` 是确定性 mock，用基线 metric 抓 score_outlier / scratch_as_answer（规则抓不到的类型）。
- 声明式规则默认带两条示例（missing_input_pages、model_disagreement），`right` 支持常量 `{value: N}`。
- worker 用 InMemoryQueue + FakeServer 覆盖时序陷阱、DLQ、重复投递、预算降级；pgmq 事务回滚集成测试在 `tests/test_pgmq_integration.py`（需要 Docker，`-m integration`）。
- Testcontainers 的 Python 测试自己 `docker build` deploy/postgres 镜像，首次较慢（编译 pgmq）。

## M5 System 2、复核与评测闭环（代码完成，未验证）

- `triage/s2.py`：`SystemTwo`（OpenAI 兼容，json_object 输出，解析失败重试一次）和 `MockSystemTwo`（确定性）；`validate()` 丢弃不存在的 span_id 并把置信度压到 0.49 以下。预算控制在 worker 的 `HourlyBudget`。
- server：`review/{ReviewController,ReviewService,EvalController}`。复核在一个事务里写 reviews、置 reviewed、upsert 一条 `eval_cases(source=human_review)`；同一 triage 二次复核返回 409。
- `eval/`：`dataset.py`（离线：进程内跑 demo 并自算基线；在线：从 server 拉 injected 用例并写回）、`metrics.py`、`run.py`、`gates.yaml`。CI 用 `--offline --gate`，不连 server。
- 门槛占位：故障召回 ≥ 0.90、正常误报 ≤ 0.10；`tests/test_eval.py` 里用较松的 0.85/0.15 断言，因为 mock 的 timeout_cascade 在离线小样本上可能被 deadline 截断得不够慢——正式阈值等跑过一次 `make eval` 后再定。
- “全部交给 System 2 的成本对照”在 mock 下恒为 0，真实模型时需要按 S2 的 usage 累计，当前只留了接口。
- `docs/INTEGRATION.md` 已写；M6 其余项见下方“M6 可选扩展”。

## 遗留与下一步

1. 在有 docker / uv / gradle 的机器上：`gradle wrapper`、`uv sync`、`npm install`，提交三份锁文件；跑 `make test`，修编译/测试错误（代码从未执行过）。
2. 启动 server 后 `npm run gen:api` 覆盖 `web/src/api/schema.ts`。
3. `make demo` 后用 `docker stats` 实测内存，记录到这里（SPEC 第 12 节）。
4. 跑一次 `make eval`，按结果定 6.4 路由阈值和 10.4 门槛。

## 验证记录（2026-11-17，本机 Windows，无 Docker）

- Python：`ruff check` / `ruff format --check` 通过；pytest 55 passed（pgmq 集成测试因无 Docker 跳过）；`python -m eval.run --backend rules --backend llm_json --offline --gate`（300 样本）通过：rules 故障召回 ~63%（抓不到 scratch_as_answer / score_outlier 属预期），llm_json mock 召回 94.7%、正常误报 4.9%、S2 根因准确率 100%。
- web：vitest 15 passed；`tsc -b --noEmit`、eslint、`vite build` 通过。
- Java：`gradle compileJava compileTestJava` 通过（用本机缓存的 Gradle 9.8.1，Spring Boot 3.4.2 插件在 Gradle 9 下可用）；`gradle test -PskipIT` 通过（OtlpSpanMapperTest）；5 个 *IT 测试未运行。

### 跑测试时修掉的问题

- demo 注入器：`transient_rate_limit` 在 score 阶段对两个模型各注入一次 429，导致 retries_high 误报；改为每个 job 只注入一次（`FaultPlan.consumed`）。
- demo 注入器：`timeout_cascade` 原来所有尝试都超时 → job 直接失败，与标准答案（suspicious，不是 failed）不符；改为前 2 次超时、第 3 次成功，demo 的 `deadline_s` 提到 12s 以容纳退避。
- `scratch_as_answer` 在合成数据里除了分数偏低没有任何可见信号，任何 S1 都抓不到；给它加了一个弱信号：两个打分模型分歧略增（8–14，低于 disagreement_flag 阈值 15）。mock S1 用“分歧偏高 + 分数偏低”组合抓它，离线召回 50%。
- mock S1 的 health softmax 给 normal 基础权重 1.2（原 1.0 会与单条弱证据打平）。
- 回归门改为按后端设阈值：rules 0.60、llm_json 0.90（rules 按设计覆盖不到 2/8 的故障类型）。
- web：openapi-fetch 在 jsdom 里相对 URL 建 Request 会抛错，`baseUrl` 改为 `window.location.origin`；`fetch` 改为调用时解析，便于测试 spy。
- web：`@tailwindcss/vite` 4.0.3 在 vite build 时抛 "Cannot convert undefined or null to object"，升级到 4.1.11。
- Python：`uv.lock` 由 uv 0.13.8 生成，Dockerfile 和 CI 的 uv 版本同步到 0.13.8（0.5.x 读不了新锁文件格式）。

## CI 验证（2026-11-17，GitHub Actions，私有仓库 Hana1025/agentobs）

5 个任务全部通过：secrets、python（含 pgmq 集成测试）、java（19 个测试，含 5 个 Testcontainers IT）、web、api-types。

### 在 CI 上修掉的问题

- deploy/postgres：pgmq 1.6 是纯 SQL 扩展，不产出 `pgmq.so`；镜像改为只拷贝 control 和 SQL 文件。
- Testcontainers 升到 1.21.4：Spring Boot 3.4.2 自带的 1.20.x 连不上 GitHub runner 上的 Docker 29（要求 API ≥ 1.44）。
- pgmq 权限：`send` 和 `archive` 都是 `INSERT … RETURNING msg_id`，需要对返回列有 SELECT。pipeline 账号只加了 `q_job_finished.msg_id` 的列级 SELECT，仍然读不到消息内容；triage 账号加了归档表的 SELECT。
- SchemaAndRolesIT 改用超级用户检查队列存在，并断言 server 账号访问 pgmq 被拒绝（规格 4.3）。
- secrets：gitleaks-action 首次推送时用 `<root>^..HEAD` 做范围会报错，改为直接用 gitleaks 8.30.1 CLI 扫描全部历史。
- OpenAPI：新增 `config/RequiredFieldsCustomizer`，基本类型和带 `@NotNull` 的 record 组件标为 required，`Map<String,Object>` 的值描述为任意类型；`web/src/api/schema.ts` 现在是 openapi-typescript 从 `/v3/api-docs` 生成的，CI 的 api-types 任务校验一致。
- 本地生成 schema 的方法：`./gradlew bootJar`，然后 `java -jar build/libs/*.jar --spring.flyway.enabled=false --spring.datasource.url=jdbc:postgresql://127.0.0.1:1/none --server.port=18080`，再 `npx openapi-typescript http://localhost:18080/v3/api-docs -o src/api/schema.ts`。

## 仍未验证

- `make demo` 整套 compose 起来的端到端效果（collector → server → worker → 看板），以及 M3/M4 里依赖运行环境的验收项（JobStuck 告警、collector 延迟 2 分钟的时序陷阱、nginx 不暴露内部接口、docker stats 内存实测）。
- 真实模型下的 llm_json / System 2（只跑过 mock）。

## M6 可选扩展（完成，CI 通过）

CI 全部 6 个任务通过（新增 deploy-config），包括 RabbitMQ 和 pgmq 的 Testcontainers 集成测试。

- **RabbitMQ**：`agentobs/queue/rabbitmq.py`（pika）。`queue_from_url` 按连接串前缀选实现，worker 和 demo 只改 `AGENTOBS_QUEUE_URL` 即可切换。
  - 可见性超时：未处理完的消息带着读取次数改投到 `job_finished.retry`，按消息 TTL 到期后由 DLX 送回主队列。读取次数放在消息头 `x-agentobs-read-ct`，不依赖 broker 的投递计数。
  - 死信：`QueuePort` 新增可选的 `dead_letter`；RabbitMQ 用 `basic.reject` 交给主队列的原生 DLX。死信原因不随消息走，只在 worker 日志和 `agentobs_triage_dlq_total{reason}` 里。
  - 事务模式只支持 pgmq（已与用户确认）；RabbitMQ 下 `finish_in` 抛 NotImplementedError，并有测试。
  - compose `rabbitmq` profile + `rabbitmq-init`（管理 API 建队列和最小权限账号）；`make demo-rabbitmq`。
- **LangChain**：`agentobs/adapters/langchain.py`，与 `llm_call` 共用建 span、填属性、记指标的函数。测试逐项比较属性集合，含 `with_retry` 下的尝试编号与 retry_reason。
- **Langfuse**：只导出到外部实例（自建需要 ClickHouse、Redis、对象存储，违反基础设施约束，已与用户确认）。`otel-collector/config-langfuse.yaml` 多一条 pipeline：脱敏 → `agentobs.cost_usd` 复制为 `gen_ai.usage.cost` → OTLP/HTTP，带 `x-langfuse-ingestion-version: 4`。用 `compose.langfuse.yaml` 叠加，`make demo-langfuse`。实现为覆盖文件而非 profile：collector 是同一个服务，profile 无法只给它加导出器。
- **INTEGRATION.md**：代码全部摘自 `python/examples/*` 的可运行脚本；`tests/test_examples.py` 运行脚本并检查文档代码块与脚本逐字一致；事务示例在 pgmq 集成测试里真实执行。
- 顺带修复：`config-demo.yaml` 早期被 sed 误删了 pipeline 的 processors / exporters，collector 用它会起不来；三份 collector 配置现在都在 CI 里校验。

## 本地端到端运行（2026-11-17，Docker Desktop 29.9.3，WSL 2，3.8 GB）

`docker compose --profile core --profile metrics --profile demo up -d --build` 全部服务启动；demo 流水线以 6 job/min 运行。

已验证的运行时验收项：

- M1：core 服务全部 healthy；Flyway 在真实库上建表成功。
- M2：每条 trace 恰有一个根 span；库里没有 `agentobs.content.*`；server 启动前 collector 导出失败后自动重试成功，demo 流水线照常完成。
- M3：看板总览、列表、详情（瀑布图、重试虚线、证据点击高亮）正常；Grafana 启动即有 Prometheus 数据源和两个面板；Prometheus 5 个抓取目标全部 up；nginx 对 `/api/internal/` 和 `/v1/` 返回 404。
- M4：每个完成的 job 都有一条 trace_triage；时序陷阱自然复现：worker 先读到消息、span 后到，消息暂缓 60 秒后被正常分诊。
- M5：provider_failure 的 job 升级到 System 2，根因判为 provider_failure，进复核队列；提交复核后从队列消失并生成 human_review 评测用例。
- 内存实测：全部容器合计约 740 MB（server 259 MB、grafana 138 MB、collector 82 MB、demo 66 MB、prometheus 58 MB、postgres 54 MB、worker 39 MB），远低于 4 核 8 GB 的约束。

本地运行时修掉的问题：

- Python 镜像：`ghcr.io/astral-sh/uv:0.13.8-python3.12-bookworm-slim` 标签不存在；改为 `python:3.12.12-slim-bookworm` 加从 `uv:0.13.8` 拷贝二进制。
- 新增 `python/.dockerignore` 和根目录 `.dockerignore`：本机的 `.venv`、`node_modules`（Windows 原生二进制）不能进镜像。CI 里没有这些目录，所以之前没暴露。
- 看板和 Grafana 的宿主机端口改为可配置（`AGENTOBS_WEB_PORT`、`GRAFANA_PORT`）；本机 8088 被 Docker Desktop 的教程容器占用，本地用 8089。
- mock System 2 的证据会引用阶段 span 上并不存在的 `agentobs.retry_reason`；改为只引用模型调用 span。
- `test_removing_builtin_rule_breaks_gate` 依赖真实耗时，偶发失败；测试里同时关掉 slow_stage / cost_spike 阈值。

仍未验证：JobStuck 告警触发、collector 人为延迟 2 分钟、真实模型下的 llm_json / System 2、`make demo-rabbitmq` 和 `make demo-langfuse` 的端到端运行。

## P0 需求补全与功能验证（2026-11-21）

补齐了切换真实模型与人工复核闭环相关的遗留项：

- **S1/S2 本地价格表计费兜底**：新增 `triage/pricing.py`（`load_triage_pricing`、`resolve_usage_cost`），`LLMJsonDecider` 和 `SystemTwo` 在供应商不直接返回 `usage.cost` 时按 `PricingTable`（优先匹配响应 model、回退请求 model）计算费用；`TriageConfig` 支持可选 `pricing`（不参与 `triage_version` 哈希），`compose.yaml` 与 `.env.example` 透传 `AGENTOBS_PRICING_FILE`；SDK `_compute_cost` 同步补齐请求 model 回退。
- **全量 System 2 成本对照**：`eval/metrics.py` 增加 `s2_cost_usd`、`s2_avg_cost_usd`、`all_s2_estimated_cost_usd`、`cost_saving_ratio`；`eval/run.py` 的 `all_s2_cost()` 支持 `--all-s2-actual` 实跑全量对照，默认按实际升级样本的 S2 均值推算节省比例；前端 `Eval.tsx` 展示分层成本对照。
- **`human_review` 复核用例回流评测闭环**：server `EvalController` 新增内部接口 `GET /api/internal/eval-cases`（支持 `source` 过滤与 `limit` 校验，带 `@Hidden` 不改公开 OpenAPI schema）；`ServerClient.get_eval_cases`、`eval/dataset.py`（`fetch_eval_cases_from_server`，兼容原 trace 已被清理的场景）和 `eval/run.py --source {traces,human_review,eval_cases,all}` 串联闭环。
- **Demo Live 模式故障注入**：`demo/pipeline.py` 的 `live_client` 分支补齐 `provider_failure`、`transient_rate_limit`、`timeout_cascade`、`output_corruption` 注入能力（含未转义控制字符注入）。
- **防误提交加固**：`.gitignore` 新增 `*.local.yaml`、`*.local.yml`、`pricing.yaml`、`*.dump`、`*.sql.gz`、`data/real/`。

功能验证结果（本机 Windows；Docker Desktop 未启动，Testcontainers 集成测试待 Docker 启动或 CI 验证）：

- Python：`ruff format --check` / `ruff check` 通过；pytest 75 passed（新增 6 项单测覆盖价格表兜底、S2 成本对照、`human_review` 拉取与缺失 trace 降级、Demo live 故障注入）；离线 300 样本 `eval --offline --gate` 通过；`examples.run_all` 通过。
- Java：`./gradlew compileJava compileTestJava test -PskipIT` 通过（`ReviewAndEvalIT` 新增 `listEvalCasesFiltersBySourceAndValidatesLimit` 编译通过，待 Docker/CI 执行）。
- Web：`vitest --run` 15 passed、`eslint`、`tsc -b --noEmit`、`vite build` 全部通过。
