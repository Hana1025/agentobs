# PROGRESS

按 `docs/SPEC.md` 第 11 节的里程碑记录：完成项、偏离规格的地方和原因、遗留问题。

## 通用说明

- 本机没有 gradle / uv / docker，代码按规格写出但**尚未运行测试**（用户要求先不测试）。
- `server/gradlew` 包装脚本和 `gradle-wrapper.jar` 未提交：需要有 gradle 的机器执行一次 `gradle wrapper --gradle-version 8.12.1` 生成；Dockerfile 和 CI 直接用 gradle 镜像/action，不依赖 wrapper。
- `python/uv.lock`、`web/package-lock.json` 未生成：首次 `uv sync` / `npm install` 后提交锁文件。
- Python 侧没有用 uv workspace，而是 `python/` 一个项目，`agentobs`、`triage`、`demo`、`eval` 为顶层包，只有 `agentobs` 进 wheel（hatch `packages`）。原因：workspace 会让 SDK 包目录多一层，与规格目录树不一致，单项目更简单且可随时拆。
