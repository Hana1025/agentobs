# agentobs

An open-source **observability and tiered failure-attribution component** for multi-stage, multi-model LLM agent pipelines.

`agentobs` provides end-to-end OpenTelemetry tracing and Prometheus metrics, automated tiered triage (deterministic rules → fast System 1 classifier → deep System 2 root-cause analysis), and a human-in-the-loop review workflow that feeds labeled cases back into offline and online evaluation benchmarks.

## Key Features

- **Low-overhead Instrumentation SDK (`python/agentobs`)**
  - Minimal API (`ao.init`, `ao.job`, `ao.stage`, `ao.llm_call` / `ao.llm_call_async`) plus an optional LangChain callback handler.
  - Single-layer retry & deadline control (`min(attempt_timeout_s, remaining_deadline)`), per-attempt spans with retry classification (`timeout`, `rate_limited`, `server_error`, `parse_error`, `connection`), and automatic output health telemetry (character count, control characters, JSON parse status).
  - **Privacy by default**: raw prompt/completion capture (`capture_content`) is disabled by default; when enabled, payloads are redacted and truncated before export and dropped at the collector by default.
  - **Fail-safe observability**: telemetry errors are logged and never crash business pipelines (except explicit transactional event emission via `job.finish_in(conn)`).
- **Tiered Failure Attribution (`python/triage`)**
  - **Layer 0 (Deterministic Rules)**: built-in rules (`job_failed`, `retries_high`, `parse_failure`, `control_chars_present`, `slow_stage`, `cost_spike`) plus YAML declarative rules comparing business-agnostic stage metrics (`agentobs.metric.*`) against rolling baselines.
  - **System 1 (Fast Triage)**: compresses each trace into a numeric-only `TraceState` (< 2k tokens, zero raw text) and scores health, suspect stage, missing input, and severity using calibrated probabilities (`rules` baseline or `llm_json` via logprobs / voting).
  - **System 2 (Deep Root-Cause Analysis)**: only escalated traces invoke a reasoning model over the full span tree, with strict `span_id` evidence validation and hourly call budget controls.
  - **Human Review → Eval Loop**: low-confidence or high-severity cases enter the review queue; reviewer decisions atomically create `eval_cases` (`source=human_review`) for repeatable benchmarking.
- **Self-Contained Deployment (`deploy/`)**
  - Runs on a single 4 vCPU / 8 GB machine with PostgreSQL 17 + `pgmq` (no Redis, Kafka, ClickHouse, or Elasticsearch required). Full demo stack uses ~740 MB RAM in Docker.
  - Works completely offline with deterministic mock models—no external API keys required for local demos or CI.

## Architecture

```text
Pipeline (Python SDK)
  ├── OTLP/HTTP ──▶ OTel Collector ──▶ Java Server (/v1/traces) ──▶ PostgreSQL (obs schema)
  ├── job_finished event ──▶ pgmq / RabbitMQ ──▶ Triage Worker (Rules → S1 → S2)
  │                                                   │
  │                                                   └──▶ Java Server (/api/internal/*)
  └── /metrics ──▶ Prometheus (+ Alertmanager) ──▶ Grafana

React Dashboard (Nginx) ──▶ Java Server (/api/*)
```

| Service | Tech Stack | Role |
| :--- | :--- | :--- |
| `pipeline` / `demo` | Python 3.12 | Business or synthetic pipeline instrumented with `agentobs` SDK |
| `otel-collector` | OTel Collector Contrib | Receives OTLP/HTTP, batches spans, enforces redaction, forwards to `server` |
| `server` | Java 21, Spring Boot 3.4 | Sole writer/reader for `obs` schema; OTLP ingest, public query/review API, internal triage/eval API |
| `triage-worker` | Python 3.12 | Stateless queue consumer running rules, System 1, routing, and System 2 |
| `postgres` | PostgreSQL 17 + `pgmq` | Stores spans, triage results, reviews, eval cases/runs, and `pgmq` queues |
| `web` | React, TypeScript, Vite | Dashboard (Overview, Trace List, SVG Waterfall Trace Detail, Review Queue, Eval Curves) served via Nginx |
| `prometheus` / `alertmanager` / `grafana` | Metrics stack | Aggregates SDK/worker/server metrics, Layer-0 alerts, and pre-provisioned dashboards |

## Quick Start

### Prerequisites

- Docker & Docker Compose v2
- (Optional, for local development) Python 3.12+ with [`uv`](https://docs.astral.sh/uv/), Java 21, Node.js 22+

### Run the Full Demo Stack (No API Key Required)

```bash
cp deploy/.env.example deploy/.env
make demo
```

This builds and starts the `core`, `metrics`, and `demo` profiles with a synthetic multi-stage pipeline (`extract → structure → score`) and fault injector running at 6 jobs/min.

- **Web Dashboard**: `http://localhost:8088` (configurable via `AGENTOBS_WEB_PORT`)
- **Grafana**: `http://localhost:3000` (configurable via `GRAFANA_PORT`, password in `deploy/.env`)

To stop all containers:

```bash
make down
```

### Enable Real LLM Models

Edit `deploy/.env` and provide an OpenAI-compatible endpoint and API key:

```dotenv
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=your-api-key
S1_MODEL=openai/gpt-4o-mini
S2_MODEL=anthropic/claude-3.5-sonnet
# Optional: local pricing table for providers that do not return usage.cost
AGENTOBS_PRICING_FILE=/path/to/pricing.yaml
```

Restart `triage-worker` (or re-run `make demo`) and it automatically switches from mock backends to live models.

## Python SDK Usage

Install the SDK in a downstream Python project:

```bash
pip install "agentobs[langchain] @ git+https://github.com/Hana1025/agentobs#subdirectory=python"
```

Minimal example (see [`docs/INTEGRATION.md`](docs/INTEGRATION.md) and [`python/examples/`](python/examples/) for runnable scripts, declarative rules, LangChain callbacks, and transactional `pgmq` publishing):

```python
import os
import agentobs as ao
from openai import OpenAI

# Always disable the underlying client's built-in retries to avoid cascading timeouts
client = OpenAI(max_retries=0)

queue_url = os.environ.get("AGENTOBS_QUEUE_URL")  # postgresql://... (pgmq) or amqp://... (RabbitMQ)
ao.init(
    service_name="downstream-pipeline",
    project_id="downstream",
    otlp_endpoint=os.environ.get("AGENTOBS_OTLP_ENDPOINT"),
    metrics_port=9464,
    capture_content=False,
    queue=ao.queue_from_url(queue_url) if queue_url else None,
    pricing="pricing.yaml",
)

with ao.job("job-0001", attributes={"pages": 12}) as job:
    with ao.stage("transcribe") as st:
        result = ao.llm_call(
            call=lambda timeout: client.chat.completions.create(
                model="vendor/model-x",
                messages=[{"role": "user", "content": "..."}],
                timeout=timeout,
            ),
            provider="openrouter",
            model="vendor/model-x",
            attempt_timeout_s=60,
            deadline_s=300,
            max_attempts=3,
            parse=ao.parsers.json_object,
        )
        # Report numeric stage metrics for rules and System 1 triage
        st.set_metric("pages_with_text", result.output["pages_with_text"])
```

## Evaluation & Regression Gates

`python/eval` measures fault recall, normal false-positive rate, per-fault recall, 10-bin ECE, reliability & coverage-accuracy curves, System 2 root-cause accuracy, and tiered vs. all-System-2 cost savings:

```bash
# Offline deterministic benchmark + CI regression gate (rules & mock llm_json)
cd python && uv run python -m eval.run --backend rules --backend llm_json --offline --gate

# Evaluate against live server traces (injected) or human-reviewed cases
make eval BACKEND=llm_json LIVE=1
cd python && uv run python -m eval.run --backend llm_json --source human_review --live
cd python && uv run python -m eval.run --backend llm_json --source all --live
```

## Optional Extensions

- **RabbitMQ Queue**: set `AGENTOBS_PIPELINE_QUEUE_URL` and `AGENTOBS_TRIAGE_QUEUE_URL` to `amqp://...` in `deploy/.env` and start with `make demo-rabbitmq` (uses retry queue TTL + native DLX).
- **External Langfuse Dual-Export**: set `LANGFUSE_OTLP_ENDPOINT`, `LANGFUSE_PUBLIC_KEY`, and `LANGFUSE_SECRET_KEY` in `deploy/.env` and start with `make demo-langfuse`.
- **LangChain Adapter**: use `agentobs.adapters.AgentObsCallbackHandler` to emit identical spans and Prometheus metrics as `ao.llm_call`.

## Repository Structure

```text
agentobs/
├── Makefile                  # dev / demo / demo-rabbitmq / demo-langfuse / test / lint / eval / gen-types
├── docs/
│   ├── SPEC.md               # Full technical specification
│   ├── PROGRESS.md           # Implementation milestones, deviations, and verification log
│   └── INTEGRATION.md        # Downstream integration guide (verified against python/examples/)
├── python/
│   ├── agentobs/             # Publishable SDK (core/, queue/, adapters/)
│   ├── triage/               # Triage worker, rules, state extractor, S1 backends, System 2
│   ├── demo/                 # Synthetic multi-stage pipeline + 8 fault injectors
│   ├── eval/                 # Evaluation runner, metrics, and CI gates
│   ├── examples/             # Runnable integration examples
│   └── tests/                # Pytest unit & Testcontainers integration tests
├── server/                   # Java 21 + Spring Boot backend (ingest, query, triage, review, config)
├── web/                      # React + TypeScript + Vite dashboard with custom SVG waterfall
└── deploy/                   # Docker Compose, Postgres+pgmq, OTel Collector, Prometheus, Grafana, Nginx, RabbitMQ
```

## Development & Testing

```bash
# Start only PostgreSQL + OTel Collector for local development
make dev

# Run all tests (Python pytest, Java JUnit + Testcontainers, Web Vitest)
make test

# Run all linters & typechecks
make lint

# Run integration examples
make examples

# Regenerate frontend TypeScript API types from a running server
make gen-types
```

## Documentation

- [Technical Specification (`docs/SPEC.md`)](docs/SPEC.md)
- [Downstream Integration Guide (`docs/INTEGRATION.md`)](docs/INTEGRATION.md)
- [Progress & Verification Log (`docs/PROGRESS.md`)](docs/PROGRESS.md)

## License

Licensed under the [Apache License 2.0](LICENSE).
