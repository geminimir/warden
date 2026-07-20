"""
Scenario 6: Cross-tenant space traversal.

    A doc is shared into Space S. A user joins S after the doc's original
    matter was walled off. Naive engine allows via the space grant.
    Warden respects the wall.

Setup:
    - Barrier b1: side_a=acme_team, side_b=zenith_team.
    - doc:shared_doc is tagged on side_b (originally a Zenith matter doc).
    - Space `collab_space` has viewer on shared_doc.
    - alice is member of both acme_team AND collab_space.
    - Naive engine sees space grant → allows.
    - Warden sees barrier → denies.
"""

from __future__ import annotations

from core.algebra import Barrier, Document, Graph, Object, Subject, Tuple, tag
from core.rebac import check
from core.store import InMemoryStore
from evals.scenarios._shared import NOW, ScenarioResult, naive_authz_check


def run() -> ScenarioResult:
    barrier = Barrier(id=1, name="acme_v_zenith", side_a="acme_team", side_b="zenith_team")
    doc = Document(
        id="shared_doc",
        org_id="firm",
        barrier_tags=frozenset({tag(1, 1)}),  # side_b tag
    )
    tuples = frozenset({
        Tuple(Subject("user", "alice"), "member", Object("group", "acme_team")),
        Tuple(Subject("user", "alice"), "member", Object("group", "collab_space")),
        Tuple(Subject("group", "collab_space"), "viewer", Object("doc", "shared_doc")),
    })
    store = InMemoryStore(
        Graph(tuples=tuples, barriers=frozenset({barrier}), documents=frozenset({doc}))
    )
    alice = Subject("user", "alice")
    target = Object("doc", "shared_doc")

    naive = naive_authz_check(store, alice, target)
    warden_ok, _ = check(store, alice, target, NOW)

    if warden_ok:
        return ScenarioResult(False, "Warden allowed shared_doc across a wall")
    if not naive:
        return ScenarioResult(False, "naive did not admit shared_doc — scenario mis-setup")
    return ScenarioResult(
        True,
        "naive: allowed via space grant; warden: denied by ethical wall on parent matter",
    )
