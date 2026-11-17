# 两个 target：triage（分诊 worker）和 demo（合成流水线）
# 官方 Python 镜像 + 从 uv 官方镜像拷贝二进制（uv 推荐写法；uv 没有 0.13.x 的 python 组合标签）
FROM python:3.12.12-slim-bookworm AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app
# 先只装依赖，利用层缓存
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project
COPY . .
RUN uv sync --no-dev --frozen
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd -m app && chown -R app /app
USER app

FROM base AS triage
EXPOSE 9465
CMD ["python", "-m", "triage.worker"]

FROM base AS demo
EXPOSE 9464
CMD ["python", "-m", "demo.run", "--rate", "6/min"]
