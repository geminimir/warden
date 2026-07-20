.PHONY: install test lint format oracle rebac labels gates api scenarios scenarios-report \
        differential differential-gate bench \
        stack-up stack-down postgres-integration pgvector-integration redis-integration \
        integration serve

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

# Everything except the Postgres integration tests. Green on a fresh clone.
test:
	.venv/bin/pytest

# Handwritten oracle fixtures — the seatbelt. MUST be green.
oracle:
	.venv/bin/pytest tests/test_oracle.py -v

# Handwritten engine fixtures. MUST be green.
rebac:
	.venv/bin/pytest tests/test_rebac.py -v

# Handwritten label / cache fixtures. MUST be green.
labels:
	.venv/bin/pytest tests/test_labels.py -v

# W3 gateway fixtures.
gates:
	.venv/bin/pytest tests/test_session.py tests/test_audit.py tests/test_gates.py -v

# FastAPI surface tests (TestClient, in-memory).
api:
	.venv/bin/pytest tests/test_api.py -v

# W3+W4 acceptance gate: all 10 adversarial scenarios.
scenarios:
	.venv/bin/pytest tests/test_scenarios.py -v

# Prints the Harvey-blog-style suite table with leak counts.
scenarios-report:
	.venv/bin/python -m evals.scenarios.run_all

# W4 benchmark tables (writes docs/bench/*.md). ~30s at CI-friendly scale.
bench:
	.venv/bin/python -m evals.bench.run

# Boot the gateway locally against in-memory backends. Useful for the demo.
serve:
	.venv/bin/uvicorn --factory gateway.demo:app --reload --port 8000

# Property tests vs. oracle at the dev default of 200 examples.
# Includes the W1 pointwise-check property and the W2 superset property.
differential:
	.venv/bin/pytest -m differential -v

# The acceptance gate: 5000 examples. Slow (~2-3 min); run before pushing.
differential-gate:
	WARDEN_HYP_MAX=5000 .venv/bin/pytest -m differential -v

stack-up:
	docker compose up -d
	@echo "Waiting for services to be ready..."
	@until docker exec warden_postgres pg_isready -U warden -d warden >/dev/null 2>&1; do sleep 1; done
	@until docker exec warden_redis redis-cli ping >/dev/null 2>&1; do sleep 1; done

stack-down:
	docker compose down -v

# W1: tuple CRUD against real Postgres.
postgres-integration: stack-up
	WARDEN_TEST_DB_URL=postgres://warden:warden@localhost:5432/warden \
	    .venv/bin/pytest tests/test_postgres_store.py -v

# W2: pgvector retrieval strategies against real Postgres+pgvector.
pgvector-integration: stack-up
	WARDEN_TEST_DB_URL=postgres://warden:warden@localhost:5432/warden \
	    .venv/bin/pytest tests/test_retrieval_pgvector.py -v

# W2: label cache against real Redis.
redis-integration: stack-up
	WARDEN_TEST_REDIS_URL=redis://localhost:6379/0 \
	    .venv/bin/pytest tests/test_labels_redis.py -v

# All integration tests. Requires Docker.
integration: stack-up
	WARDEN_TEST_DB_URL=postgres://warden:warden@localhost:5432/warden \
	WARDEN_TEST_REDIS_URL=redis://localhost:6379/0 \
	    .venv/bin/pytest tests/test_postgres_store.py \
	                     tests/test_retrieval_pgvector.py \
	                     tests/test_labels_redis.py -v

lint:
	.venv/bin/ruff check .
	.venv/bin/mypy core evals

format:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .
