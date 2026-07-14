"""
Handwritten fixture tests for the reference oracle.

The oracle is the ground truth. It doesn't get bugs from clever caching or a
subtle graph-walk optimization, because it has neither. Every scenario here is
a specific failure mode of a naive implementation.

If any of these fail, the oracle is wrong. Do not fix the test.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.algebra import (
    Barrier,
    Document,
    Graph,
    Object,
    Subject,
    Tuple,
    tag,
)
from core.oracle import Oracle

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _tuple(subject: str, relation: str, obj: str, expires_at: datetime | None = None) -> Tuple:
    """Concise tuple factory: '(subject_type:subject_id)-relation->(object_type:object_id)'."""
    s_type, s_id = subject.split(":", 1)
    o_type, o_id = obj.split(":", 1)
    return Tuple(
        subject=Subject(type=s_type, id=s_id),  # type: ignore[arg-type]
        relation=relation,  # type: ignore[arg-type]
        object=Object(type=o_type, id=o_id),  # type: ignore[arg-type]
        expires_at=expires_at,
    )


def _oracle(tuples: list[Tuple], docs: list[Document], barriers: list[Barrier] = None) -> Oracle:
    return Oracle(
        Graph(
            tuples=frozenset(tuples),
            barriers=frozenset(barriers or []),
            documents=frozenset(docs),
        )
    )


# ---------------------------------------------------------------------------
# Baseline: a single direct grant works.
# ---------------------------------------------------------------------------

def test_direct_viewer_grant_allows() -> None:
    doc = Document(id="d1", org_id="acme")
    oracle = _oracle(
        tuples=[_tuple("user:alice", "viewer", "doc:d1")],
        docs=[doc],
    )
    allowed, reason = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert allowed
    assert reason.decision == "allow"
    assert len(reason.steps) == 1


def test_no_grant_denies() -> None:
    doc = Document(id="d1", org_id="acme")
    oracle = _oracle(tuples=[], docs=[doc])
    allowed, reason = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not allowed
    assert reason.decision == "deny"


# ---------------------------------------------------------------------------
# Nesting: 5-hop grant through nested groups must be found.
# ---------------------------------------------------------------------------

def test_transitive_5_hop_group_nesting() -> None:
    # alice -member-> g1 -member-> g2 -member-> g3 -member-> g4 -viewer-> d1
    doc = Document(id="d1", org_id="acme")
    tuples = [
        _tuple("user:alice", "member", "group:g1"),
        _tuple("group:g1", "member", "group:g2"),
        _tuple("group:g2", "member", "group:g3"),
        _tuple("group:g3", "member", "group:g4"),
        _tuple("group:g4", "viewer", "doc:d1"),
    ]
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, reason = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert allowed
    assert len(reason.steps) == 5


# ---------------------------------------------------------------------------
# Cycles: A member-of B member-of A must terminate, not loop forever.
# ---------------------------------------------------------------------------

def test_cyclic_membership_terminates() -> None:
    doc = Document(id="d1", org_id="acme")
    tuples = [
        _tuple("user:alice", "member", "group:a"),
        _tuple("group:a", "member", "group:b"),
        _tuple("group:b", "member", "group:a"),  # cycle
        _tuple("group:a", "viewer", "doc:d1"),
    ]
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, _ = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert allowed


def test_cyclic_membership_no_grant_denies_without_looping() -> None:
    """Cycle exists, but no grant exists. Must return deny promptly."""
    doc = Document(id="d1", org_id="acme")
    tuples = [
        _tuple("user:alice", "member", "group:a"),
        _tuple("group:a", "member", "group:b"),
        _tuple("group:b", "member", "group:a"),
    ]
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, _ = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not allowed


# ---------------------------------------------------------------------------
# Deny dominates: even with a firm-wide grant, an ethical wall blocks.
# ---------------------------------------------------------------------------

def test_ethical_wall_overrides_grant() -> None:
    # A firm-wide viewer grant would normally let alice see d1.
    # But d1 is walled off from alice's team.
    barrier = Barrier(id=1, name="acme-vs-zenith", side_a="acme_team", side_b="zenith_team")
    doc = Document(id="d1", org_id="firm", barrier_tags=frozenset({tag(1, 1)}))  # doc on side B
    tuples = [
        _tuple("user:alice", "member", "group:acme_team"),
        _tuple("user:alice", "member", "group:firm_all"),
        _tuple("group:firm_all", "viewer", "doc:d1"),  # firm-wide grant
    ]
    oracle = _oracle(tuples=tuples, docs=[doc], barriers=[barrier])
    allowed, reason = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not allowed
    assert reason.decision == "deny"
    assert reason.barrier_hit == barrier


def test_principal_on_both_sides_of_barrier_is_blocked_from_both() -> None:
    """A user in both sides of a barrier is blocked from EVERY tagged doc on
    either side. Symmetric deny — the pathological case that breaks naive
    "assume one side" implementations."""
    barrier = Barrier(id=7, name="wall", side_a="team_a", side_b="team_b")
    doc_a = Document(id="da", org_id="firm", barrier_tags=frozenset({tag(7, 0)}))
    doc_b = Document(id="db", org_id="firm", barrier_tags=frozenset({tag(7, 1)}))
    tuples = [
        _tuple("user:mallory", "member", "group:team_a"),
        _tuple("user:mallory", "member", "group:team_b"),
        _tuple("user:mallory", "viewer", "doc:da"),
        _tuple("user:mallory", "viewer", "doc:db"),
    ]
    oracle = _oracle(tuples=tuples, docs=[doc_a, doc_b], barriers=[barrier])
    for d_id in ("da", "db"):
        allowed, _ = oracle.check(Subject("user", "mallory"), Object("doc", d_id), NOW)
        assert not allowed, f"expected mallory blocked from {d_id}"


# ---------------------------------------------------------------------------
# Expiry: time-boxed guest access.
# ---------------------------------------------------------------------------

def test_expired_tuple_denies() -> None:
    doc = Document(id="d1", org_id="acme")
    yesterday = NOW - timedelta(days=1)
    tuples = [_tuple("user:guest", "viewer", "doc:d1", expires_at=yesterday)]
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, _ = oracle.check(Subject("user", "guest"), Object("doc", "d1"), NOW)
    assert not allowed


def test_future_expiry_still_allows() -> None:
    doc = Document(id="d1", org_id="acme")
    tomorrow = NOW + timedelta(days=1)
    tuples = [_tuple("user:guest", "viewer", "doc:d1", expires_at=tomorrow)]
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, _ = oracle.check(Subject("user", "guest"), Object("doc", "d1"), NOW)
    assert allowed


def test_expiry_on_intermediate_hop_breaks_the_chain() -> None:
    """One hop expired, the rest permanent. Must deny — the chain is only as
    live as its weakest link."""
    doc = Document(id="d1", org_id="acme")
    yesterday = NOW - timedelta(days=1)
    tuples = [
        _tuple("user:alice", "member", "group:g1"),
        _tuple("group:g1", "member", "group:g2", expires_at=yesterday),  # expired hop
        _tuple("group:g2", "viewer", "doc:d1"),
    ]
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, _ = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not allowed


# ---------------------------------------------------------------------------
# Multiple grant paths: revoke one, the others still grant.
# ---------------------------------------------------------------------------

def test_three_grant_paths_one_revoked_still_allows() -> None:
    doc = Document(id="d1", org_id="acme")
    tuples = [
        # Path 1: direct viewer
        _tuple("user:alice", "viewer", "doc:d1"),
        # Path 2: through group A (this one is the "revoked" — omitted from the
        # graph to simulate revocation)
        # Path 3: through org membership
        _tuple("user:alice", "member", "org:acme"),
        _tuple("org:acme", "owner", "doc:d1"),
    ]
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, _ = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert allowed


def test_all_grant_paths_revoked_denies() -> None:
    doc = Document(id="d1", org_id="acme")
    oracle = _oracle(tuples=[], docs=[doc])
    allowed, _ = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not allowed


# ---------------------------------------------------------------------------
# Depth limit: MAX_DEPTH is a hard ceiling.
# ---------------------------------------------------------------------------

def test_depth_limit_is_enforced() -> None:
    """Build a 20-hop chain; MAX_DEPTH is 8, so alice must not reach the doc."""
    doc = Document(id="d1", org_id="acme")
    tuples = [_tuple("user:alice", "member", "group:g0")]
    for i in range(20):
        tuples.append(_tuple(f"group:g{i}", "member", f"group:g{i + 1}"))
    tuples.append(_tuple("group:g20", "viewer", "doc:d1"))
    oracle = _oracle(tuples=tuples, docs=[doc])
    allowed, _ = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not allowed


# ---------------------------------------------------------------------------
# authorized_set: bulk API used by the differential harness.
# ---------------------------------------------------------------------------

def test_authorized_set_returns_only_reachable_docs() -> None:
    d1 = Document(id="d1", org_id="acme")
    d2 = Document(id="d2", org_id="acme")
    d3 = Document(id="d3", org_id="acme")
    tuples = [
        _tuple("user:alice", "viewer", "doc:d1"),
        _tuple("user:alice", "viewer", "doc:d3"),
        # d2 unreachable
    ]
    oracle = _oracle(tuples=tuples, docs=[d1, d2, d3])
    assert oracle.authorized_set(Subject("user", "alice"), NOW) == {"d1", "d3"}


def test_authorized_set_excludes_barrier_blocked_docs() -> None:
    barrier = Barrier(id=1, name="wall", side_a="a", side_b="b")
    d_open = Document(id="open", org_id="firm")
    d_walled = Document(id="walled", org_id="firm", barrier_tags=frozenset({tag(1, 1)}))
    tuples = [
        _tuple("user:alice", "member", "group:a"),  # puts alice on side A; blocked from side B
        _tuple("user:alice", "viewer", "doc:open"),
        _tuple("user:alice", "viewer", "doc:walled"),  # grant exists but wall wins
    ]
    oracle = _oracle(tuples=tuples, docs=[d_open, d_walled], barriers=[barrier])
    assert oracle.authorized_set(Subject("user", "alice"), NOW) == {"open"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
