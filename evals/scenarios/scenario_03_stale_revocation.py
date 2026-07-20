"""
Scenario 3: Stale revocation.

    Access was revoked between the last label-materialization and this
    query. The pre-filter (Gate 1) still thinks alice can see the doc.
    Gate 2 catches it.

Setup:
    - alice is member of group `g`; `g` has viewer on doc:d1.
    - Label materialization ran BEFORE the revocation, so acl_labels for
      d1 still includes label(g), and L(alice) still includes label(g).
    - Someone then removes the g→viewer→d1 tuple. Cache not invalidated.
    - Gate 1 says "d1 passes the label filter" (over-permissive by design).
    - Gate 2 (fresh check() against the current graph) says deny.

Assertion: Gate 2 catches the stale-revocation leak. This is the whole
architectural justification for the fail-closed authoritative gate.
"""

from __future__ import annotations

from core.algebra import Graph, Object, Subject, Tuple
from core.labels import label_for_subject, materialize_doc_labels
from core.rebac import check
from core.store import InMemoryStore
from evals.scenarios._shared import NOW, ScenarioResult


def run() -> ScenarioResult:
    # Pre-revocation state.
    before = InMemoryStore(
        Graph(
            tuples=frozenset({
                Tuple(Subject("user", "alice"), "member", Object("group", "g")),
                Tuple(Subject("group", "g"), "viewer", Object("doc", "d1")),
            }),
            barriers=frozenset(),
            documents=frozenset(),
        )
    )
    # Snapshot the doc labels BEFORE revoke — this is what a stale cache holds.
    d1_labels_before = materialize_doc_labels(before, "d1")

    # Post-revocation: the group grant is gone.
    after = InMemoryStore(
        Graph(
            tuples=frozenset({
                Tuple(Subject("user", "alice"), "member", Object("group", "g")),
            }),
            barriers=frozenset(),
            documents=frozenset(),
        )
    )

    alice = Subject("user", "alice")
    # Simulate the stale pre-filter: alice's L(u) still contains label(g),
    # and d1's acl_labels (cached snapshot) still contains label(g), so the
    # label predicate `acl_labels && L(u)` is satisfied. Gate 1 admits d1.
    gate1_admits = bool(d1_labels_before & {label_for_subject(Subject("group", "g"))})

    # Gate 2 against the CURRENT graph. Should deny.
    gate2_ok, _ = check(after, alice, Object("doc", "d1"), NOW)

    if not gate1_admits:
        return ScenarioResult(False, "setup: stale filter didn't admit d1 — scenario mis-setup")
    if gate2_ok:
        return ScenarioResult(False, "Warden Gate 2 allowed d1 after revocation — LEAK")
    return ScenarioResult(
        True,
        "stale pre-filter admitted d1 (as designed); Gate 2 denied — fail-closed held",
    )
