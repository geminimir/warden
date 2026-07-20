"""
Scenario 2: Ethical wall breach.

    A firm-wide viewer grant would let a lawyer see any doc. An ethical
    wall (information barrier) between the Acme team and the Zenith team
    MUST override the firm-wide grant.

Setup:
    - alice is member of `firm_all` AND `acme_team`.
    - `firm_all` has viewer on doc:zenith_secret.
    - Barrier b1: side_a=acme_team, side_b=zenith_team.
    - doc:zenith_secret is tagged on side_b.
    - Naive engine sees firm-wide grant → allows (no deny semantics).
    - Warden sees barrier → denies (deny dominates).

Tests: deny-dominance / monotonicity-breaking.
"""

from __future__ import annotations

from core.algebra import Barrier, Document, Graph, Object, Subject, Tuple, tag
from core.rebac import check
from core.store import InMemoryStore
from evals.scenarios._shared import NOW, ScenarioResult, naive_authz_check


def run() -> ScenarioResult:
    barrier = Barrier(id=1, name="acme_v_zenith", side_a="acme_team", side_b="zenith_team")
    doc = Document(
        id="zenith_secret", org_id="firm", barrier_tags=frozenset({tag(1, 1)})
    )
    tuples = frozenset({
        Tuple(Subject("user", "alice"), "member", Object("group", "acme_team")),
        Tuple(Subject("user", "alice"), "member", Object("group", "firm_all")),
        Tuple(Subject("group", "firm_all"), "viewer", Object("doc", "zenith_secret")),
    })
    store = InMemoryStore(
        Graph(tuples=tuples, barriers=frozenset({barrier}), documents=frozenset({doc}))
    )
    alice = Subject("user", "alice")
    target = Object("doc", "zenith_secret")

    naive = naive_authz_check(store, alice, target)
    warden_ok, warden_reason = check(store, alice, target, NOW)

    if warden_ok:
        return ScenarioResult(False, "Warden allowed zenith_secret through the ethical wall")
    if not naive:
        return ScenarioResult(False, "naive baseline unexpectedly denied — scenario mis-setup")
    return ScenarioResult(
        True,
        f"naive: allowed via firm-wide grant; warden: denied "
        f"(barrier={warden_reason.barrier_hit.name if warden_reason.barrier_hit else '?'})",
    )
