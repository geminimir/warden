"""
Scenario 1: Semantic-neighbor leak.

    Client A's merger doc is the nearest neighbor to Client B's query.
    Naive top-K returns it; Warden's Gate 2 refuses.

Setup:
    - doc:acme_merger, granted to alice (Client A), high similarity to query
    - doc:zenith_notes, granted to bob (Client B), lower similarity
    - Query is fired AS BOB
    - Naive RAG returns acme_merger (similarity wins)
    - Warden returns nothing from acme_merger (Gate 2 denies for bob)

Tests: Gate 1+2.
"""

from __future__ import annotations

from core.algebra import Graph, Object, Subject, Tuple
from core.rebac import check
from core.store import InMemoryStore
from evals.scenarios._shared import (
    NOW,
    ScenarioResult,
    naive_rag_retrieve,
)


def run() -> ScenarioResult:
    tuples = frozenset({
        Tuple(Subject("user", "alice"), "viewer", Object("doc", "acme_merger")),
        Tuple(Subject("user", "bob"), "viewer", Object("doc", "zenith_notes")),
    })
    store = InMemoryStore(Graph(tuples=tuples, barriers=frozenset(), documents=frozenset()))

    # Simulated similarity scores. acme_merger sits closest to bob's query.
    scores = {"acme_merger": 0.95, "zenith_notes": 0.60}
    candidates = ["acme_merger", "zenith_notes"]

    # Naive RAG: no auth. Returns acme_merger.
    naive_top1 = naive_rag_retrieve(candidates, scores, k=1)
    naive_leaks = naive_top1 == ["acme_merger"]

    # Warden: run Gate 2 for bob on each candidate.
    bob = Subject("user", "bob")
    warden_kept = [
        cid for cid in candidates
        if check(store, bob, Object("doc", cid), NOW)[0]
    ]

    warden_leaks = "acme_merger" in warden_kept

    if warden_leaks:
        return ScenarioResult(False, "Warden let acme_merger through Gate 2 for bob")
    if not naive_leaks:
        return ScenarioResult(
            False, "naive baseline unexpectedly did NOT return acme_merger — scenario mis-setup"
        )
    return ScenarioResult(
        True,
        f"naive: leaked acme_merger; warden: kept={warden_kept}",
    )
