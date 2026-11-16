# PROGRESS

按 `docs/SPEC.md` 第 11 节的里程碑记录：完成项、偏离规格的地方和原因、遗留问题。

## 通用说明

- 本机没有 gradle / uv / docker，代码按规格写出但**尚未运行测试**（用户要求先不测试）。
- `server/gradlew` 包装脚本和 `gradle-wrapper.jar` 未提交：需要有 gradle 的机器执行一次 `gradle wrapper --gradle-version 8.12.1` 生成；Dockerfile 和 CI 直接用 gradle 镜像/action，不依赖 wrapper。
- `python/uv.lock`、`web/package-lock.json` 未生成：首次 `uv sync` / `npm install` 后提交锁文件。
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
- `docs/INTEGRATION.md` 已写（M6 的一项）；RabbitMQ、LangChain 适配器、langfuse profile 未做。

## 遗留与下一步

1. 在有 docker / uv / gradle 的机器上：`gradle wrapper`、`uv sync`、`npm install`，提交三份锁文件；跑 `make test`，修编译/测试错误（代码从未执行过）。
2. 启动 server 后 `npm run gen:api` 覆盖 `web/src/api/schema.ts`。
3. `make demo` 后用 `docker stats` 实测内存，记录到这里（SPEC 第 12 节）。
4. 跑一次 `make eval`，按结果定 6.4 路由阈值和 10.4 门槛。
