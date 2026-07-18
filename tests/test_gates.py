"""
Gate 2 + Gate 3 tests. In-memory backends throughout — the FastAPI layer
sits on top and does not add new authorization semantics.

These fixtures verify the *behavioural* invariants:

    Gate 2: every candidate produces exactly one audit row per call site
    Gate 3: revoked docs are evicted; the audit log records the eviction
    Gate 3: cited-but-revoked docs are returned in `stripped`
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.algebra import Graph, Object, Subject, Tuple
from core.store import InMemoryStore
from gateway.audit import InMemoryAuditLog
from gateway.gates import (
    gate2_check,
    gate3_revalidate,
    gate3_verify_citations,
    retrieve_authorized,
)
from gateway.session import InMemorySessionStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _t(subject: str, relation: str, obj: str) -> Tuple:
    s_type, s_id = subject.split(":", 1)
    o_type, o_id = obj.split(":", 1)
    return Tuple(
        subject=Subject(type=s_type, id=s_id),  # type: ignore[arg-type]
        relation=relation,  # type: ignore[arg-type]
        object=Object(type=o_type, id=o_id),  # type: ignore[arg-type]
    )


def _make(tuples: list[Tuple]) -> tuple[InMemoryStore, InMemoryAuditLog, InMemorySessionStore]:
    store = InMemoryStore(
        Graph(tuples=frozenset(tuples), barriers=frozenset(), documents=frozenset())
    )
    return store, InMemoryAuditLog(), InMemorySessionStore()


# ---------------------------------------------------------------------------
# Gate 2
# ---------------------------------------------------------------------------

def test_gate2_allows_and_audits() -> None:
    store, audit, _ = _make([_t("user:alice", "viewer", "doc:d1")])
    ok, _ = gate2_check(
        store, audit, Subject("user", "alice"), "d1",
        action="retrieve", gate=2, session_id="s1",
    )
    assert ok
    entries = audit.all()
    assert len(entries) == 1
    assert entries[0].decision == "allow"


def test_gate2_denies_and_audits() -> None:
    store, audit, _ = _make([])  # no grants
    ok, _ = gate2_check(
        store, audit, Subject("user", "alice"), "d1",
        action="retrieve", gate=2, session_id="s1",
    )
    assert not ok
    assert audit.all()[0].decision == "deny"


# ---------------------------------------------------------------------------
# retrieve_authorized: end-to-end Gate 2 + session add
# ---------------------------------------------------------------------------

def test_retrieve_authorized_adds_only_allowed_refs() -> None:
    store, audit, sessions = _make(
        [
            _t("user:alice", "viewer", "doc:allowed"),
            _t("user:bob", "viewer", "doc:not_alice"),  # alice cannot see this
        ]
    )
    session = sessions.create(Subject("user", "alice"))
    kept = retrieve_authorized(
        store, audit, sessions, session.session_id,
        candidate_doc_ids=["allowed", "not_alice"],
    )
    assert [r.doc_id for r in kept] == ["allowed"]
    assert [r.doc_id for r in sessions.list_refs(session.session_id)] == ["allowed"]
    # Both docs produced an audit row (allow + deny) — denies are
    # compliance-interesting.
    decisions = [e.decision for e in audit.all()]
    assert decisions == ["allow", "deny"]


# ---------------------------------------------------------------------------
# Gate 3 — the scenario that no other system passes
# ---------------------------------------------------------------------------

def test_gate3_evicts_revoked_docs_and_audits() -> None:
    # Alice has viewer on d1 initially → gets added to context.
    store, audit, sessions = _make([_t("user:alice", "viewer", "doc:d1")])
    session = sessions.create(Subject("user", "alice"))
    retrieve_authorized(
        store, audit, sessions, session.session_id, candidate_doc_ids=["d1"],
    )
    assert [r.doc_id for r in sessions.list_refs(session.session_id)] == ["d1"]

    # REVOKE: rebuild the store without alice's grant.
    revoked_store = InMemoryStore(
        Graph(tuples=frozenset(), barriers=frozenset(), documents=frozenset())
    )
    evicted = gate3_revalidate(revoked_store, audit, sessions, session.session_id)

    assert [r.doc_id for r in evicted] == ["d1"]
    assert sessions.list_refs(session.session_id) == []
    # Audit trail includes the original allow (retrieve), a context_hold
    # deny at Gate 3, and an evict row.
    actions = [(e.action, e.decision, e.gate) for e in audit.all()]
    assert ("retrieve", "allow", 2) in actions
    assert ("context_hold", "deny", 3) in actions
    assert ("evict", "deny", 3) in actions


def test_gate3_no_op_when_nothing_revoked() -> None:
    store, audit, sessions = _make([_t("user:alice", "viewer", "doc:d1")])
    session = sessions.create(Subject("user", "alice"))
    retrieve_authorized(
        store, audit, sessions, session.session_id, candidate_doc_ids=["d1"],
    )
    evicted = gate3_revalidate(store, audit, sessions, session.session_id)
    assert evicted == []
    assert [r.doc_id for r in sessions.list_refs(session.session_id)] == ["d1"]


# ---------------------------------------------------------------------------
# Gate 3 — citation stripping
# ---------------------------------------------------------------------------

def test_verify_citations_strips_revoked() -> None:
    store, audit, sessions = _make([_t("user:alice", "viewer", "doc:live")])
    session = sessions.create(Subject("user", "alice"))
    # Model tries to cite two docs: one it can still see, one it can't.
    stripped = gate3_verify_citations(
        store, audit, sessions, session.session_id,
        cited_doc_ids=["live", "gone"],
    )
    assert stripped == ["gone"]


def test_verify_citations_all_allowed_returns_empty() -> None:
    store, audit, sessions = _make([_t("user:alice", "viewer", "doc:live")])
    session = sessions.create(Subject("user", "alice"))
    stripped = gate3_verify_citations(
        store, audit, sessions, session.session_id,
        cited_doc_ids=["live"],
    )
    assert stripped == []
