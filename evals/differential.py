"""
Differential harness: compare the real engine to the reference oracle.

The three properties this checks — the same three from Part 4 of the design doc
— apply to every implementation the harness runs against. In W0, no engine
exists yet, so this file's tests fail. **That is the correct state for W0
acceptance.** Once W1 lands the real `check()`, wire it into `_engine_check` /
`_engine_authorized_set` below and the tests should go green across at least
5,000 generated graphs.

    Read this as a spec, not as a work item. The pluggable seam is the engine
    adapter functions at the bottom of this file.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import HealthCheck, given, settings

from core.algebra import Graph, Object, Subject
from core.oracle import Oracle, all_principals
from evals.generators import NOW, authz_graphs


# ---------------------------------------------------------------------------
# Engine adapter (the seam that W1 plugs into)
# ---------------------------------------------------------------------------

class EngineNotImplemented(Exception):
    """Raised until the real engine (W1) exists. That's the correct W0 state."""


def _engine_check(_graph: Graph, _principal: Subject, _obj: Object, _at: datetime) -> bool:
    """Placeholder for the real engine's check(). Wire W1's rebac.check() here."""
    raise EngineNotImplemented(
        "core.rebac.check() does not exist yet. W1 is where this gets wired up; "
        "until then the differential tests fail on purpose — that's how you know "
        "the harness is real."
    )


def _engine_authorized_set(_graph: Graph, _principal: Subject, _at: datetime) -> set[str]:
    """Placeholder for the real engine's bulk API."""
    raise EngineNotImplemented("W1 not landed yet.")


# ---------------------------------------------------------------------------
# The three properties
# ---------------------------------------------------------------------------

# Hypothesis settings tuned for this suite:
#   - max_examples: 200 in dev; CI can crank this up (design doc: >= 5,000 for
#     the W1 acceptance gate).
#   - deadline: disabled — the oracle is O(n^2) by design and can be slow on
#     large graphs. That's fine; correctness is the point.
_hyp = settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

# xfail with strict=True + raises=EngineNotImplemented is deliberate:
#   - today, the tests raise EngineNotImplemented -> XFAIL -> build stays green.
#   - the moment W1 wires a real check() into `_engine_check`, these tests will
#     start passing -> XPASS -> strict=True fails the build, forcing us to
#     remove the marker and turn them into real gates. That's the whole point.
_pending_engine = pytest.mark.xfail(
    reason="requires W1 engine (core/rebac.py) — see #4/#5/#6",
    raises=EngineNotImplemented,
    strict=True,
)


@pytest.mark.differential
@_pending_engine
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
@_pending_engine
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
@_pending_engine
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
