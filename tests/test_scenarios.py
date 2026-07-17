"""
End-to-end scenario tests.

These are the W3 acceptance gate. If either fails, the milestone is not
shipped — no exceptions.
"""

from __future__ import annotations

from evals.scenarios import scenario_04_inflight_revocation, scenario_10_prompt_injection


def test_scenario_04_inflight_revocation() -> None:
    r = scenario_04_inflight_revocation.run()
    assert r.passed, r.details


def test_scenario_10_prompt_injection() -> None:
    r = scenario_10_prompt_injection.run()
    assert r.passed, r.details
