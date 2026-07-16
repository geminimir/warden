"""
Wires evals/differential.py into pytest collection.

The property tests fail until W1 lands the real engine. That failure IS the
W0 acceptance signal: the harness is real, it's calling into a seam that
doesn't exist yet, and it will start protecting the codebase the moment
someone plugs `core.rebac.check()` in.

We import the test functions here so `pytest tests/` picks them up alongside
the oracle fixture tests.
"""

from evals.differential import (  # noqa: F401
    test_fidelity_engine_returns_everything_the_oracle_allows,
    test_label_filter_is_permissive_superset,
    test_pointwise_check_matches_oracle,
    test_safety_engine_never_returns_docs_the_oracle_denies,
)
