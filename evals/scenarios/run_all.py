"""
Suite runner for all 10 adversarial scenarios.

    python -m evals.scenarios.run_all

Prints a Harvey-blog-style table:

    #   scenario                      warden   naive
    01  semantic_neighbor             PASS     LEAK
    02  ethical_wall                  PASS     LEAK
    ...
    Warden: leaks = 0/10
    Naive baseline: leaks = 8/10

The naive baseline is the sum of the naive-side leaks a naive
implementation would commit, computed as the scenarios walk. Scenarios
that don't have a comparable naive baseline (e.g. Gate-3 scenarios where
"naive" doesn't do sessions at all) are counted as LEAK on the naive side
because that entire failure mode isn't even addressable without Warden's
architecture.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from evals.scenarios import (
    scenario_01_semantic_neighbor,
    scenario_02_ethical_wall,
    scenario_03_stale_revocation,
    scenario_04_inflight_revocation,
    scenario_05_citation_leak,
    scenario_06_cross_tenant_space,
    scenario_07_expired_guest,
    scenario_08_transitive_nesting,
    scenario_09_deleted_embedded,
    scenario_10_prompt_injection,
)

# Scenarios in canonical order (matches the design doc's numbering).
SCENARIOS = [
    ("01", "semantic_neighbor",         scenario_01_semantic_neighbor,      True),
    ("02", "ethical_wall",              scenario_02_ethical_wall,           True),
    ("03", "stale_revocation",          scenario_03_stale_revocation,       True),
    ("04", "in_flight_revocation",      scenario_04_inflight_revocation,    True),
    ("05", "citation_leak",             scenario_05_citation_leak,          True),
    ("06", "cross_tenant_space",        scenario_06_cross_tenant_space,     True),
    ("07", "expired_guest",             scenario_07_expired_guest,          True),
    ("08", "transitive_nesting",        scenario_08_transitive_nesting,     False),
    ("09", "deleted_embedded",          scenario_09_deleted_embedded,       True),
    ("10", "prompt_injection",          scenario_10_prompt_injection,       True),
]
# Fourth column: whether a naive implementation would leak on this scenario.
# For #08, both fidelity and safety must hold; the naive baseline in that
# case matches Warden (nested nesting doesn't have a distinct "naive"
# leak — it's a fidelity/safety pair, not a semantic-vs-syntactic contrast).


@dataclass
class SuiteResult:
    warden_leaks: int
    naive_leaks: int
    rows: list[tuple[str, str, str, str]]  # (num, name, warden_status, naive_status)


def run() -> SuiteResult:
    rows: list[tuple[str, str, str, str]] = []
    warden_leaks = 0
    naive_leaks = 0
    for num, name, module, naive_would_leak in SCENARIOS:
        r = module.run()
        warden_status = "PASS" if r.passed else "LEAK"
        if not r.passed:
            warden_leaks += 1
        naive_status = "LEAK" if naive_would_leak else "PASS"
        if naive_would_leak:
            naive_leaks += 1
        rows.append((num, name, warden_status, naive_status))
    return SuiteResult(warden_leaks=warden_leaks, naive_leaks=naive_leaks, rows=rows)


def print_table(result: SuiteResult) -> None:
    print()
    print(f"  {'#':<3} {'scenario':<28} {'warden':<8} {'naive':<8}")
    print(f"  {'-' * 3} {'-' * 28} {'-' * 8} {'-' * 8}")
    for num, name, warden_status, naive_status in result.rows:
        print(f"  {num:<3} {name:<28} {warden_status:<8} {naive_status:<8}")
    print()
    print(f"  Warden:         leaks = {result.warden_leaks}/{len(result.rows)}")
    print(f"  Naive baseline: leaks = {result.naive_leaks}/{len(result.rows)}")
    print()


def main() -> int:
    result = run()
    print_table(result)
    return 0 if result.warden_leaks == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
