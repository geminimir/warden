"""
End-to-end scenario tests — all 10 of the adversarial suite.

The W4 acceptance gate: Warden = 0/10. If any of these turn LEAK, the
milestone is not shipped — no exceptions.
"""

from __future__ import annotations

import pytest

from evals.scenarios.run_all import SCENARIOS, run


@pytest.mark.parametrize(
    "num,name,module,_naive_leaks",
    SCENARIOS,
    ids=[f"{n}_{name}" for (n, name, _, _) in SCENARIOS],
)
def test_scenario_warden_passes(num, name, module, _naive_leaks) -> None:
    r = module.run()
    assert r.passed, f"scenario {num} ({name}) FAILED: {r.details}"


def test_suite_warden_leaks_zero() -> None:
    """The headline number. Warden must be 0/10 across the whole suite."""
    result = run()
    assert result.warden_leaks == 0, (
        f"Warden leaked on {result.warden_leaks} scenarios: "
        f"{[r for r in result.rows if r[2] == 'LEAK']}"
    )


def test_suite_naive_baseline_leaks() -> None:
    """Sanity check that the naive baseline is actually broken. If this
    fails, the scenarios aren't distinguishing Warden from a naive stack
    — the whole comparison is meaningless."""
    result = run()
    assert result.naive_leaks > 0, "naive baseline unexpectedly passed everything"
