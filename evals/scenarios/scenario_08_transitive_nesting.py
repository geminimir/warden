"""
Scenario 8: Transitive nesting (fidelity + safety).

    A grant reachable through 5 hops of nested group membership MUST be
    found by the engine (fidelity — silent recall loss would be a bug).

    When one intermediate hop is revoked, the grant MUST propagate
    (safety — a stale allow would be a leak).

Both halves must hold. This is the only scenario that asserts BOTH
directions in one shot.
"""

from __future__ import annotations

from core.algebra import Graph, Object, Subject, Tuple
from core.rebac import check
from core.store import InMemoryStore
from evals.scenarios._shared import NOW, ScenarioResult


def _make_5_hop_store(intermediate_expired: bool) -> InMemoryStore:
    """alice -member-> g0 -member-> g1 -> g2 -> g3 -> g4 -viewer-> d1.

    If `intermediate_expired`, drop the g2->g3 edge (simulate revoke).
    """
    from datetime import timedelta

    yesterday = NOW - timedelta(days=1)
    edges: list[Tuple] = [
        Tuple(Subject("user", "alice"), "member", Object("group", "g0")),
        Tuple(Subject("group", "g0"), "member", Object("group", "g1")),
        Tuple(Subject("group", "g1"), "member", Object("group", "g2")),
        Tuple(
            Subject("group", "g2"), "member", Object("group", "g3"),
            expires_at=yesterday if intermediate_expired else None,
        ),
        Tuple(Subject("group", "g3"), "member", Object("group", "g4")),
        Tuple(Subject("group", "g4"), "viewer", Object("doc", "d1")),
    ]
    return InMemoryStore(
        Graph(tuples=frozenset(edges), barriers=frozenset(), documents=frozenset())
    )


def run() -> ScenarioResult:
    alice = Subject("user", "alice")
    target = Object("doc", "d1")

    # Fidelity: 5-hop chain must resolve.
    live_ok, live_reason = check(_make_5_hop_store(False), alice, target, NOW)
    if not live_ok:
        return ScenarioResult(False, "fidelity FAIL: 5-hop grant not found by Warden")
    if len(live_reason.steps) != 6:
        return ScenarioResult(
            False, f"expected 6-step reason path, got {len(live_reason.steps)}"
        )

    # Safety: with the middle hop dead, must deny.
    revoked_ok, _ = check(_make_5_hop_store(True), alice, target, NOW)
    if revoked_ok:
        return ScenarioResult(False, "safety FAIL: revoked mid-chain grant still allowed")

    return ScenarioResult(
        True,
        "fidelity: 5-hop chain resolved; safety: mid-chain revocation propagated",
    )
