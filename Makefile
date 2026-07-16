.PHONY: install test lint format oracle rebac differential differential-gate \
        postgres-up postgres-down postgres-integration

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

# Property tests vs. oracle at the dev default of 200 examples.
differential:
	.venv/bin/pytest -m differential -v

# The W1 acceptance gate: 5000 examples. Slow (~2 min); run before pushing.
differential-gate:
	WARDEN_HYP_MAX=5000 .venv/bin/pytest -m differential -v

postgres-up:
	docker compose up -d postgres
	@echo "Waiting for Postgres to be ready..."
	@until docker compose exec postgres pg_isready -U warden -d warden >/dev/null 2>&1; do sleep 1; done

postgres-down:
	docker compose down -v

# Integration tests for PostgresStore. Requires `make postgres-up` first
# (or WARDEN_TEST_DB_URL set to a running instance).
postgres-integration: postgres-up
	WARDEN_TEST_DB_URL=postgres://warden:warden@localhost:5432/warden \
	    .venv/bin/pytest tests/test_postgres_store.py -v

lint:
	.venv/bin/ruff check .
	.venv/bin/mypy core evals

format:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .
