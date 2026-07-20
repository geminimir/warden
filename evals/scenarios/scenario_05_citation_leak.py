"""
Scenario 5: Citation leak.

    The model synthesized its answer BEFORE the revocation landed, then
    tried to render a citation to the now-forbidden doc. Gate 3's citation
    verifier strips it.

Setup:
    - alice retrieves doc:d1 (allowed).
    - Model composes an answer that cites d1.
    - Access revoked before the answer is rendered.
    - gate3_verify_citations is called; d1 ends up in `stripped`.
"""

from __future__ import annotations

from core.algebra import Graph, Object, Subject, Tuple
from core.store import InMemoryStore
from gateway.api import _rebuild_in_memory_store
from gateway.audit import InMemoryAuditLog
from gateway.gates import gate3_verify_citations, retrieve_authorized
from gateway.session import InMemorySessionStore
from evals.scenarios._shared import ScenarioResult


def run() -> ScenarioResult:
    grant = Tuple(Subject("user", "alice"), "viewer", Object("doc", "d1"))
    store = InMemoryStore(
        Graph(tuples=frozenset({grant}), barriers=frozenset(), documents=frozenset())
    )
    audit = InMemoryAuditLog()
    sessions = InMemorySessionStore()
    session = sessions.create(Subject("user", "alice"))

    retrieve_authorized(store, audit, sessions, session.session_id, ["d1"])
    # Model composed its answer citing d1. Now revocation lands.
    _rebuild_in_memory_store(store, remove=grant)

    stripped = gate3_verify_citations(
        store, audit, sessions, session.session_id, cited_doc_ids=["d1"]
    )

    if stripped != ["d1"]:
        return ScenarioResult(False, f"expected d1 stripped; got {stripped}")
    return ScenarioResult(True, "citation to revoked doc was stripped by Gate 3")
