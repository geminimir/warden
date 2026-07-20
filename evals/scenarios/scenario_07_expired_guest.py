"""
Scenario 7: Expired guest access.

    External counsel is granted viewer access with expires_at=yesterday.
    Naive engine (no expiry check) still allows.
    Warden denies — the tuple is dead.
"""

from __future__ import annotations

from core.algebra import Graph, Object, Subject, Tuple
from core.rebac import check
from core.store import InMemoryStore
from evals.scenarios._shared import NOW, YESTERDAY, ScenarioResult, naive_authz_check


def run() -> ScenarioResult:
    tuples = frozenset({
        Tuple(
            Subject("user", "external_counsel"),
            "viewer",
            Object("doc", "matter_brief"),
            expires_at=YESTERDAY,
        ),
    })
    store = InMemoryStore(Graph(tuples=tuples, barriers=frozenset(), documents=frozenset()))
    principal = Subject("user", "external_counsel")
    target = Object("doc", "matter_brief")

    naive = naive_authz_check(store, principal, target)  # ignores expiry
    warden_ok, _ = check(store, principal, target, NOW)  # honours expiry

    if warden_ok:
        return ScenarioResult(False, "Warden allowed an expired guest grant")
    if not naive:
        return ScenarioResult(False, "naive did not admit expired grant — mis-setup")
    return ScenarioResult(True, "naive: ignored expiry; warden: denied expired grant")
