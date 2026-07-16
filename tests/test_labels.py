"""
Handwritten fixture tests for label materialization and cache semantics.

The invariant these enforce, informally:

    A user has access to a doc  ⇔  materialize_doc_labels(doc) ∩ L(u) ≠ ∅

    (subject to the barrier check, tested separately)

Any fixture where a user CAN reach a doc via oracle.check but their labels
DON'T overlap the doc's is a silent recall bug — the differential property
test catches those at scale, these fixtures catch specific known shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.algebra import Barrier, Document, Graph, Object, Subject, Tuple, tag
from core.labels import (
    InMemoryCache,
    label_for_subject,
    labels_for,
    labels_for_cached,
    materialize_doc_labels,
)
from core.store import InMemoryStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _t(subject: str, relation: str, obj: str) -> Tuple:
    s_type, s_id = subject.split(":", 1)
    o_type, o_id = obj.split(":", 1)
    return Tuple(
        subject=Subject(type=s_type, id=s_id),  # type: ignore[arg-type]
        relation=relation,  # type: ignore[arg-type]
        object=Object(type=o_type, id=o_id),  # type: ignore[arg-type]
    )


def _store(
    tuples: list[Tuple],
    docs: list[Document] | None = None,
    barriers: list[Barrier] | None = None,
) -> InMemoryStore:
    return InMemoryStore(
        Graph(
            tuples=frozenset(tuples),
            barriers=frozenset(barriers or []),
            documents=frozenset(docs or []),
        )
    )


# ---------------------------------------------------------------------------
# label_for_subject: determinism + namespace separation
# ---------------------------------------------------------------------------

def test_label_is_deterministic() -> None:
    s = Subject("group", "engineering")
    assert label_for_subject(s) == label_for_subject(s)


def test_labels_disjoint_across_types() -> None:
    """A user 'x' and a group 'x' must never collide — same id, different type."""
    u = label_for_subject(Subject("user", "x"))
    g = label_for_subject(Subject("group", "x"))
    o = label_for_subject(Subject("org", "x"))
    assert len({u, g, o}) == 3


def test_labels_fit_in_31_bit_int() -> None:
    """acl_labels is int[] in Postgres (int4 = signed 32-bit). Every label
    must be a valid positive int4."""
    for s in [Subject("user", "u1"), Subject("group", "g" * 100), Subject("org", "o")]:
        n = label_for_subject(s)
        assert 0 < n < 2**31


# ---------------------------------------------------------------------------
# materialize_doc_labels: doc-side forward walk
# ---------------------------------------------------------------------------

def test_materialize_direct_user_grant() -> None:
    store = _store([_t("user:alice", "viewer", "doc:d1")])
    labels = materialize_doc_labels(store, "d1")
    assert labels == {label_for_subject(Subject("user", "alice"))}


def test_materialize_group_grant() -> None:
    store = _store([_t("group:g1", "viewer", "doc:d1")])
    labels = materialize_doc_labels(store, "d1")
    assert labels == {label_for_subject(Subject("group", "g1"))}


def test_materialize_owner_and_viewer_both_count() -> None:
    store = _store(
        [
            _t("user:alice", "viewer", "doc:d1"),
            _t("user:bob", "owner", "doc:d1"),
        ]
    )
    labels = materialize_doc_labels(store, "d1")
    assert labels == {
        label_for_subject(Subject("user", "alice")),
        label_for_subject(Subject("user", "bob")),
    }


def test_materialize_ignores_non_grant_relations() -> None:
    """member/parent do not confer read access on a doc — they must not
    show up in acl_labels."""
    store = _store(
        [
            _t("user:alice", "member", "doc:d1"),  # nonsense but shouldn't materialize
            _t("group:g1", "viewer", "doc:d1"),
        ]
    )
    labels = materialize_doc_labels(store, "d1")
    assert labels == {label_for_subject(Subject("group", "g1"))}


def test_materialize_doc_not_in_store() -> None:
    """A doc id with no incident tuples yields the empty label set."""
    store = _store([_t("user:alice", "viewer", "doc:other")])
    assert materialize_doc_labels(store, "missing") == set()


# ---------------------------------------------------------------------------
# labels_for: principal-side reverse walk (L and B)
# ---------------------------------------------------------------------------

def test_L_contains_the_principal_itself() -> None:
    store = _store([_t("user:alice", "viewer", "doc:d1")])
    L, _ = labels_for(store, Subject("user", "alice"))
    assert label_for_subject(Subject("user", "alice")) in L


def test_L_contains_transitive_group_membership() -> None:
    store = _store(
        [
            _t("user:alice", "member", "group:g1"),
            _t("group:g1", "member", "group:g2"),
            _t("group:g2", "member", "group:g3"),
        ]
    )
    L, _ = labels_for(store, Subject("user", "alice"))
    for gid in ("g1", "g2", "g3"):
        assert label_for_subject(Subject("group", gid)) in L


def test_L_contains_org_membership() -> None:
    store = _store([_t("user:alice", "member", "org:acme")])
    L, _ = labels_for(store, Subject("user", "alice"))
    assert label_for_subject(Subject("org", "acme")) in L


def test_B_contains_opposite_barrier_side() -> None:
    """A user on side_a is blocked by tags on side_b, encoded as tag(id, 1)."""
    barrier = Barrier(id=1, name="wall", side_a="team_a", side_b="team_b")
    store = _store(
        [_t("user:alice", "member", "group:team_a")],
        barriers=[barrier],
    )
    _, B = labels_for(store, Subject("user", "alice"))
    assert tag(1, 1) in B
    assert tag(1, 0) not in B


def test_B_symmetric_when_principal_on_both_sides() -> None:
    """A principal in BOTH sides is blocked from both — B contains both tags."""
    barrier = Barrier(id=7, name="wall", side_a="a", side_b="b")
    store = _store(
        [
            _t("user:mallory", "member", "group:a"),
            _t("user:mallory", "member", "group:b"),
        ],
        barriers=[barrier],
    )
    _, B = labels_for(store, Subject("user", "mallory"))
    assert tag(7, 0) in B
    assert tag(7, 1) in B


# ---------------------------------------------------------------------------
# The key composition: acl_labels ∩ L(u) ≠ ∅  ⇔  u has some grant path to d
# ---------------------------------------------------------------------------

def test_composition_matches_direct_grant() -> None:
    store = _store([_t("user:alice", "viewer", "doc:d1")])
    doc_labels = materialize_doc_labels(store, "d1")
    L, _ = labels_for(store, Subject("user", "alice"))
    assert doc_labels & L


def test_composition_matches_transitive_group_grant() -> None:
    store = _store(
        [
            _t("user:alice", "member", "group:g1"),
            _t("group:g1", "member", "group:g2"),
            _t("group:g2", "viewer", "doc:d1"),
        ]
    )
    doc_labels = materialize_doc_labels(store, "d1")
    L, _ = labels_for(store, Subject("user", "alice"))
    assert doc_labels & L


def test_composition_no_overlap_when_unrelated() -> None:
    store = _store(
        [
            _t("user:alice", "viewer", "doc:d1"),
            _t("user:bob", "member", "group:g_bob"),  # unrelated
        ]
    )
    doc_labels = materialize_doc_labels(store, "d1")
    L, _ = labels_for(store, Subject("user", "bob"))
    assert not (doc_labels & L)


# ---------------------------------------------------------------------------
# LabelCache semantics
# ---------------------------------------------------------------------------

def test_cache_populates_on_miss() -> None:
    cache = InMemoryCache()
    store = _store([_t("user:alice", "member", "group:g1")])
    assert cache.get(Subject("user", "alice")) is None
    L, B = labels_for_cached(store, cache, Subject("user", "alice"))
    assert cache.get(Subject("user", "alice")) == (L, B)


def test_cache_returns_stale_after_write_without_invalidate() -> None:
    """This is the buggy state we want to be able to detect. A grant is
    added to the store but nobody invalidated the cache — labels_for_cached
    returns the OLD, under-permissive L. The correct write path calls
    invalidate; this test proves the cache would otherwise be silently
    wrong."""
    cache = InMemoryCache()
    store1 = _store([_t("user:alice", "member", "group:g1")])
    L_before, _ = labels_for_cached(store1, cache, Subject("user", "alice"))
    # Simulate a new grant landing in the store WITHOUT invalidation:
    store2 = _store(
        [
            _t("user:alice", "member", "group:g1"),
            _t("user:alice", "member", "group:g2"),  # new grant
        ]
    )
    L_stale, _ = labels_for_cached(store2, cache, Subject("user", "alice"))
    assert L_stale == L_before  # still returning pre-write labels


def test_cache_invalidate_forces_recompute() -> None:
    cache = InMemoryCache()
    store1 = _store([_t("user:alice", "member", "group:g1")])
    labels_for_cached(store1, cache, Subject("user", "alice"))
    # New grant + explicit invalidate (the correct write path):
    store2 = _store(
        [
            _t("user:alice", "member", "group:g1"),
            _t("user:alice", "member", "group:g2"),
        ]
    )
    cache.invalidate(Subject("user", "alice"))
    L_fresh, _ = labels_for_cached(store2, cache, Subject("user", "alice"))
    assert label_for_subject(Subject("group", "g2")) in L_fresh


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
