# 两个 target：triage（分诊 worker）和 demo（合成流水线）
FROM ghcr.io/astral-sh/uv:0.5.26-python3.12-bookworm-slim AS base
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock* README.md ./
RUN uv sync --no-dev --frozen --no-install-workspace || uv sync --no-dev --no-install-workspace
COPY . .
RUN uv sync --no-dev --frozen || uv sync --no-dev
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd -m app && chown -R app /app
USER app

FROM base AS triage
EXPOSE 9465
CMD ["python", "-m", "triage.worker"]

FROM base AS demo
EXPOSE 9464
CMD ["python", "-m", "demo.run", "--rate", "6/min"]
