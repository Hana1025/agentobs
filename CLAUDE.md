# agentobs

开源的 LLM agent 可观测与分层归因组件。

**以 `docs/SPEC.md` 为准。** 规格之外的细节选最简单、可替换的做法并记入 `docs/PROGRESS.md`；与规格冲突时停下来问。

## 工作方式
- 按 SPEC 第 11 节的里程碑顺序做，一次一个里程碑；完成后更新 `docs/PROGRESS.md`。
- 小步提交，Conventional Commits（feat/fix/chore/test/docs）。

## 硬性约束
- 不加规格外的基础设施组件（Redis、Kafka、ClickHouse、Elasticsearch 等）。
- 仓库里不出现任何真实业务数据，只用合成数据；业务项目只称“下游项目”。
- 密钥只走环境变量；`deploy/.env.example` 列出全部变量，`.env` 已在 `.gitignore`。
- SDK `capture_content` 默认 False；开启时先脱敏再发出。
- 所有模型调用都有 mock 实现，CI 和 `make demo` 不需要任何 API key。
- SDK 内任何异常只记日志，不抛给调用方（事务模式发事件除外）。
- 每个模块有测试：Python pytest、Java JUnit + Testcontainers、前端 Vitest。

## 目录
- `python/` uv workspace：`agentobs`（SDK）、`triage`（分诊 worker）、`demo`、`eval`、`tests`
- `server/` Spring Boot（Gradle Kotlin DSL），包 `dev.agentobs.{ingest,query,triage,review,config}`
- `web/` React + Vite + TypeScript
- `deploy/` compose、postgres、otel-collector、prometheus、alertmanager、grafana、nginx

## 常用命令
`make dev` / `make demo` / `make test` / `make eval` / `make lint`
