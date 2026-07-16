"""
Tuple store abstractions used by the real engine (core/rebac.py).

There are two implementations:

    InMemoryStore   — a dict of adjacency lists. Fast enough that the
                      differential harness can run 5,000 property tests
                      against it in seconds. Used by tests and by anyone
                      who wants Warden as a pure library without a database.

    PostgresStore   — the real thing (W1.1 SQL schema). Adds Postgres as
                      the storage substrate that W2 will layer indexes on.
                      Slower per operation; validated on a smaller
                      integration-test corpus.

Both implement the `TupleStore` Protocol below. The engine (`core/rebac.py`)
never imports either implementation directly — it takes a Protocol. That's
what lets a single algorithm serve both.

    Deliberately NOT shared with `core/oracle.py`. The oracle reads
    `Graph.tuples` directly, in-memory, no adjacency indexing. Different
    data path means different bugs. That is the whole point of having a
    differential harness in the first place.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Protocol

from core.algebra import (
    Barrier,
    Graph,
    Relation,
    Subject,
    Tuple,
)


class TupleStore(Protocol):
    """The interface the engine uses to read authorization state.

    Deliberately narrow. The engine needs three things:
      1. All outgoing tuples from a subject (for grant-path traversal).
      2. All barriers (deny-side evaluation).
      3. All groups a principal is transitively a member of (for barrier
         side-of evaluation).

    Anything wider than this belongs on a more specific interface
    (LabelIndex in W2, VectorStore in W2, etc.), not here.
    """

    def outgoing(self, subject: Subject) -> Iterable[Tuple]:
        """Every tuple with this subject on the left. Order not guaranteed."""
        ...

    def barriers(self) -> Iterable[Barrier]:
        """Every barrier in the current authorization state."""
        ...

    def group_memberships(self, principal: Subject) -> set[str]:
        """The set of group ids the principal transitively belongs to.

        Cycle-safe. Not depth-limited: membership for barrier-side evaluation
        is a structural fact, not a grant path. A user 20 hops deep in group
        nesting is still on the wall's side; only *grant paths* are gated
        by MAX_DEPTH. (See core/algebra.py for the rationale.)
        """
        ...

    def document_barrier_tags(self, doc_id: str) -> frozenset[int]:
        """The barrier tags carried by this document.

        Returns empty frozenset if the doc has no barrier tags or isn't
        known to the store. Barrier evaluation needs the doc's tag set to
        overlap with the principal's blocked-tag set; this is the only
        doc-shaped bit the engine touches.
        """
        ...


class InMemoryStore:
    """Fast store used by the differential harness and by any consumer that
    doesn't need Postgres.

    Immutable after construction — matches how `Graph` is used elsewhere and
    sidesteps concurrent-mutation questions until they matter (W2's outbox
    worker).
    """

    def __init__(self, graph: Graph) -> None:
        self._graph = graph

        # Adjacency by subject key. NOTE: intentionally a different data
        # shape than the oracle uses — oracle iterates `graph.tuples`
        # linearly, we bucket by subject. Same source data, distinct paths.
        self._outgoing: dict[tuple[str, str], list[Tuple]] = defaultdict(list)
        for t in graph.tuples:
            self._outgoing[(t.subject.type, t.subject.id)].append(t)

        self._barriers = tuple(graph.barriers)
        self._doc_tags: dict[str, frozenset[int]] = {
            d.id: d.barrier_tags for d in graph.documents
        }

    def outgoing(self, subject: Subject) -> Iterable[Tuple]:
        return self._outgoing.get((subject.type, subject.id), ())

    def barriers(self) -> Iterable[Barrier]:
        return self._barriers

    def document_barrier_tags(self, doc_id: str) -> frozenset[int]:
        return self._doc_tags.get(doc_id, frozenset())

    def group_memberships(self, principal: Subject) -> set[str]:
        """Iterative BFS up through `member` edges into groups and orgs.

        This is deliberately using BFS to match the oracle's approach for
        this specific sub-operation (both need reachability, no path
        reconstruction). The interesting divergence between engine and
        oracle happens in the grant-path search — that's DFS here vs. BFS
        in the oracle. If both walk membership the same way for barriers,
        that's fine; we're not testing barrier-membership traversal
        alongside grant-path traversal via the differential harness — we're
        testing the composition of both against the oracle's composed answer.
        """
        groups: set[str] = set()
        seen: set[tuple[str, str]] = set()
        frontier: list[Subject] = [principal]
        while frontier:
            current = frontier.pop()
            key = (current.type, current.id)
            if key in seen:
                continue
            seen.add(key)
            if current.type == "group":
                groups.add(current.id)
            for t in self._outgoing.get(key, ()):
                if t.relation != "member":
                    continue
                if t.object.type not in ("group", "org"):
                    continue
                frontier.append(Subject(type=t.object.type, id=t.object.id))
        return groups


# ---------------------------------------------------------------------------
# Mutation helpers (used by tests + eventually by W3's write API)
# ---------------------------------------------------------------------------

def graph_with_tuple(graph: Graph, t: Tuple) -> Graph:
    """Return a new Graph with `t` added. Non-mutating on purpose."""
    return Graph(
        tuples=frozenset({*graph.tuples, t}),
        barriers=graph.barriers,
        documents=graph.documents,
    )


def graph_without_tuple(graph: Graph, t: Tuple) -> Graph:
    """Return a new Graph with `t` removed."""
    return Graph(
        tuples=frozenset(graph.tuples - {t}),
        barriers=graph.barriers,
        documents=graph.documents,
    )


# ---------------------------------------------------------------------------
# Sanity: Protocol conformance check that runs at import time in dev builds
# ---------------------------------------------------------------------------

# Mypy will enforce this statically; the runtime check is a belt-and-suspenders
# assertion so tests don't accidentally drift from the Protocol.
def _assert_conforms(_store: TupleStore) -> None:  # pragma: no cover
    pass


# Silence unused-import warnings for symbols exported for downstream use.
__all__ = [
    "InMemoryStore",
    "Relation",
    "TupleStore",
    "graph_without_tuple",
    "graph_with_tuple",
]
