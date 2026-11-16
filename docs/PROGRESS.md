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
