COMPOSE := docker compose -f deploy/compose.yaml --env-file deploy/.env
BACKEND ?= rules
LIVE ?= 0

.PHONY: dev demo down test test-python test-java test-web lint lint-python lint-java lint-web eval gen-types

dev:          ## 只起 postgres 和 collector，server / web / worker 本地跑
	$(COMPOSE) --profile dev up -d postgres otel-collector

demo:         ## core + metrics + demo 一键启动
	$(COMPOSE) --profile core --profile metrics --profile demo up -d --build

down:
	$(COMPOSE) --profile core --profile metrics --profile demo --profile dev down

test: test-python test-java test-web

test-python:
	cd python && uv run pytest -q

test-java:
	cd server && ./gradlew test --no-daemon

test-web:
	cd web && npm test -- --run

lint: lint-python lint-java lint-web

lint-python:
	cd python && uv run ruff check . && uv run ruff format --check .

lint-java:
	cd server && ./gradlew check -x test --no-daemon

lint-web:
	cd web && npm run lint && npm run typecheck

eval:         ## make eval BACKEND=llm_json LIVE=1
	cd python && uv run python -m eval.run --backend $(BACKEND) $(if $(filter 1,$(LIVE)),--live,)

gen-types:    ## 从运行中的 server 生成前端类型
	cd web && npm run gen:api
