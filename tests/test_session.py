"""
Session store fixtures. Cover the mutation invariants that Gate 3 depends on:

    - add is idempotent per doc_id (no duplicate refs on repeated retrieve)
    - evict removes and returns exactly the requested docs
    - list returns a copy (external mutation must not corrupt state)
    - session_id is unguessable (spot check length)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.algebra import ReasonPath, Subject
from gateway.session import DocRef, InMemorySessionStore, SessionNotFound

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _ref(doc_id: str, org_id: str = "acme") -> DocRef:
    return DocRef(
        doc_id=doc_id,
        org_id=org_id,
        added_at=NOW,
        granted_by=ReasonPath.allow(()),
    )


def test_create_returns_session_with_bound_principal() -> None:
    store = InMemorySessionStore()
    session = store.create(Subject("user", "alice"))
    assert session.principal == Subject("user", "alice")
    assert session.doc_refs == []


def test_session_id_has_url_safe_entropy() -> None:
    store = InMemorySessionStore()
    ids = {store.create(Subject("user", "a")).session_id for _ in range(50)}
    # 50 distinct — probabilistic but essentially certain at 128 bits.
    assert len(ids) == 50
    # URL-safe token_urlsafe(16) produces at least 21 chars.
    assert all(len(sid) >= 21 for sid in ids)


def test_add_refs_is_idempotent_per_doc_id() -> None:
    store = InMemorySessionStore()
    s = store.create(Subject("user", "alice"))
    store.add_refs(s.session_id, [_ref("d1"), _ref("d1"), _ref("d2")])
    ids = [r.doc_id for r in store.list_refs(s.session_id)]
    assert ids == ["d1", "d2"]


def test_evict_removes_and_returns_requested_docs() -> None:
    store = InMemorySessionStore()
    s = store.create(Subject("user", "alice"))
    store.add_refs(s.session_id, [_ref(f"d{i}") for i in range(4)])
    evicted = store.evict_refs(s.session_id, ["d1", "d3"])
    remaining_ids = [r.doc_id for r in store.list_refs(s.session_id)]
    assert {r.doc_id for r in evicted} == {"d1", "d3"}
    assert remaining_ids == ["d0", "d2"]


def test_evict_missing_doc_id_is_noop() -> None:
    store = InMemorySessionStore()
    s = store.create(Subject("user", "alice"))
    store.add_refs(s.session_id, [_ref("d1")])
    evicted = store.evict_refs(s.session_id, ["not_present", "d1"])
    assert {r.doc_id for r in evicted} == {"d1"}


def test_list_refs_returns_a_copy_not_a_reference() -> None:
    """Callers mutating the returned list must not corrupt the store."""
    store = InMemorySessionStore()
    s = store.create(Subject("user", "alice"))
    store.add_refs(s.session_id, [_ref("d1")])
    got = store.list_refs(s.session_id)
    got.append(_ref("evil"))
    assert [r.doc_id for r in store.list_refs(s.session_id)] == ["d1"]


def test_missing_session_raises() -> None:
    store = InMemorySessionStore()
    with pytest.raises(SessionNotFound):
        store.list_refs("nonexistent")
