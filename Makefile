.PHONY: install dev format lint type-check test check mock-demo benchmark compare-agents report verify-install verify-tianyancha docker-build docker-run
.PHONY: local ecs-package agentarts
.PHONY: local-start local-restart local-stop

install:
	uv sync --frozen --group dev --extra agentarts

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run ruff format --check .

type-check:
	uv run mypy src tests

test:
	uv run pytest

check: lint type-check test

dev:
	uv run uvicorn jindiao.api.app:app --host 0.0.0.0 --port 8000 --reload

mock-demo:
	uv run python scripts/run_mock_demo.py

benchmark:
	uv run python scripts/compare_agents.py --manifest benchmarks/manifest.json --output benchmarks/results/latest

compare-agents: benchmark

report: benchmark

verify-install:
	uv run python scripts/verify_installation.py

verify-tianyancha:
	uv run python scripts/verify_tianyancha_integration.py

docker-build:
	docker build -t jindiao:local .

docker-run:
	docker compose up --build

# Deployment arguments contain paths/options only, never credential values.
local:
	./bin/local $(ARGS)

local-start:
	./bin/start.sh $(ARGS)

local-restart:
	./bin/restart.sh $(ARGS)

local-stop:
	./bin/stop.sh $(ARGS)

ecs-package:
	./bin/ecs-package $(ARGS)

agentarts:
	./bin/agentarts $(ARGS)
