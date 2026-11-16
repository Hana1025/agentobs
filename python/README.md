# agentobs Python workspace

- `agentobs/` 可发布的埋点 SDK
- `triage/` 分诊 worker
- `demo/` 合成流水线 + 故障注入
- `eval/` 评测脚本
- `tests/` pytest

```bash
uv sync --all-packages
uv run pytest -q
uv run python -m demo.run --batch 20 --seed 42
```
