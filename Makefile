.PHONY: install test lint format oracle differential

install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"

test:
	.venv/bin/pytest

# Only the handwritten oracle fixture tests. These MUST be green.
oracle:
	.venv/bin/pytest tests/test_oracle.py -v

# Property tests vs. oracle. Expected to FAIL until W1 lands the real engine.
# That failure is the correct state for W0 acceptance.
differential:
	.venv/bin/pytest -m differential -v || true

lint:
	.venv/bin/ruff check .
	.venv/bin/mypy core evals

format:
	.venv/bin/ruff format .
	.venv/bin/ruff check --fix .
