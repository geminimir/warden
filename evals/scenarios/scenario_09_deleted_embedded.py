"""
Scenario 9: Deleted-but-embedded.

    A doc has been deleted from the source-of-truth (tuple deleted, doc
    removed from the graph), but its embedding is still in the vector
    index. A naive top-K on the vector index would return it.

    Warden's Gate 2 runs check() against the CURRENT graph. The tuple is
    gone → deny.
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
    # Doc is gone from the graph (empty tuple set).
    store = InMemoryStore(
        Graph(tuples=frozenset(), barriers=frozenset(), documents=frozenset())
    )
    # But the vector index still has the embedding — simulate.
    stale_index_candidates = ["orphaned_doc"]
    scores = {"orphaned_doc": 0.99}

    naive_top1 = naive_rag_retrieve(stale_index_candidates, scores, k=1)
    naive_leaks = naive_top1 == ["orphaned_doc"]

    principal = Subject("user", "alice")
    warden_kept = [
        c for c in stale_index_candidates
        if check(store, principal, Object("doc", c), NOW)[0]
    ]

    if warden_kept:
        return ScenarioResult(False, f"Warden returned deleted-but-embedded doc: {warden_kept}")
    if not naive_leaks:
        return ScenarioResult(False, "naive didn't return orphaned doc — mis-setup")
    return ScenarioResult(
        True,
        "naive: returned orphaned doc from stale index; warden: denied at Gate 2",
    )
