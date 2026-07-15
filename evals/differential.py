"""
Differential harness: compare the real engine to the reference oracle.

Every property test in this file compares `core.rebac` against `core.oracle`
across randomly generated authorization graphs. If they ever disagree on a
green build, one of them is wrong — most likely rebac, because oracle is
brute-force and has no room to hide bugs.

    Wire is: oracle vs. rebac. The two implementations share nothing but the
    types in core/algebra.py. Different data structures, different traversal
    algorithms (BFS vs. DFS), different code paths for barrier evaluation.
    A bug common to both would have to originate in algebra.py, which is
    small enough to eyeball.
"""

from __future__ import annotations

import os
from datetime import datetime

import pytest
from hypothesis import HealthCheck, given, settings

from core.algebra import Graph, Object, Subject
from core.oracle import Oracle, all_principals
from core.rebac import authorized_set as rebac_authorized_set
from core.rebac import check as rebac_check
from core.store import InMemoryStore
from evals.generators import NOW, authz_graphs


# ---------------------------------------------------------------------------
# Engine adapter (kept as a seam so a PostgresStore-backed engine can be
# swapped in for the W1.1 integration job without duplicating the harness).
# ---------------------------------------------------------------------------

def _engine_check(graph: Graph, principal: Subject, obj: Object, at: datetime) -> bool:
    ok, _ = rebac_check(InMemoryStore(graph), principal, obj, at)
    return ok


def _engine_authorized_set(graph: Graph, principal: Subject, at: datetime) -> set[str]:
    return rebac_authorized_set(InMemoryStore(graph), principal, at, graph.documents)


# ---------------------------------------------------------------------------
# The three properties
# ---------------------------------------------------------------------------

# Hypothesis settings tuned for this suite:
#   - max_examples: 200 by default (fast local dev loop).
#     Override via WARDEN_HYP_MAX for CI — the design doc names 5000 as the
#     W1 acceptance gate; the CI workflow sets that value.
#   - deadline: disabled — the oracle is O(n^2) by design and can be slow on
#     large graphs. That's fine; correctness is the point.
_MAX_EXAMPLES = int(os.environ.get("WARDEN_HYP_MAX", "200"))

_hyp = settings(
    max_examples=_MAX_EXAMPLES,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

@pytest.mark.differential
@_hyp
@given(graph=authz_graphs())
def test_safety_engine_never_returns_docs_the_oracle_denies(graph: Graph) -> None:
    """SAFETY (G1): nothing outside the oracle's authorized set may reach the model.

    This is the leak-prevention property. If it ever fails on a green build,
    Warden has a hole.
    """
    oracle = Oracle(graph)
    for principal in all_principals(graph):
        oracle_set = oracle.authorized_set(principal, NOW)
        engine_set = _engine_authorized_set(graph, principal, NOW)
        leaked = engine_set - oracle_set
        assert not leaked, (
            f"LEAK: engine returned docs {leaked} for {principal} that oracle denied.\n"
            f"  oracle_set = {sorted(oracle_set)}\n"
            f"  engine_set = {sorted(engine_set)}"
        )


@pytest.mark.differential
@_hyp
@given(graph=authz_graphs())
def test_fidelity_engine_returns_everything_the_oracle_allows(graph: Graph) -> None:
    """FIDELITY (G2): every doc the oracle allows must also come back from the engine.

    Silent recall loss is invisible to users, so nothing else catches this.
    It's the property that keeps a paranoid engine from being useless.
    """
    oracle = Oracle(graph)
    for principal in all_principals(graph):
        oracle_set = oracle.authorized_set(principal, NOW)
        engine_set = _engine_authorized_set(graph, principal, NOW)
        missing = oracle_set - engine_set
        assert not missing, (
            f"LOST: engine dropped docs {missing} for {principal} that oracle allowed."
        )


@pytest.mark.differential
@_hyp
@given(graph=authz_graphs())
def test_pointwise_check_matches_oracle(graph: Graph) -> None:
    """check(u, d) agrees with the oracle for every principal-doc pair.

    Redundant with safety+fidelity in aggregate, but it localizes failures: if
    this fails on a specific (u, d), the failing pair is exactly the reason.
    """
    oracle = Oracle(graph)
    for principal in all_principals(graph):
        for doc in graph.documents:
            oracle_allow, _ = oracle.check(principal, Object("doc", doc.id), NOW)
            engine_allow = _engine_check(graph, principal, Object("doc", doc.id), NOW)
            assert engine_allow == oracle_allow, (
                f"DISAGREEMENT on ({principal}, doc:{doc.id}): "
                f"oracle={oracle_allow}, engine={engine_allow}"
            )
