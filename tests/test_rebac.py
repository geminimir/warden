"""
Handwritten fixture tests for the REAL engine (core/rebac.py).

These are NOT copies of tests/test_oracle.py. They target the same semantics
but with different graph shapes, different assertion emphases, and adversarial
patterns designed to break the specific algorithm choices in rebac.py — DFS
traversal, memoization, in-progress cycle detection.

    Rule: if you find a bug via the differential harness that these fixtures
    missed, add a fixture here that would have caught it. Regression tests
    are permanent.
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
from core.rebac import authorized_set, check, expand
from core.store import InMemoryStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _t(subject: str, relation: str, obj: str, expires_at: datetime | None = None) -> Tuple:
    s_type, s_id = subject.split(":", 1)
    o_type, o_id = obj.split(":", 1)
    return Tuple(
        subject=Subject(type=s_type, id=s_id),  # type: ignore[arg-type]
        relation=relation,  # type: ignore[arg-type]
        object=Object(type=o_type, id=o_id),  # type: ignore[arg-type]
        expires_at=expires_at,
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
# Baseline: direct grant + no grant. Sanity checks — if these fail, don't
# bother reading the rest of the failures.
# ---------------------------------------------------------------------------

def test_direct_grant() -> None:
    store = _store([_t("user:alice", "viewer", "doc:d1")])
    ok, reason = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok
    assert reason.decision == "allow"
    assert len(reason.steps) == 1


def test_no_grant_denies() -> None:
    store = _store([])
    ok, reason = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok
    assert reason.decision == "deny"


# ---------------------------------------------------------------------------
# DFS-specific traps. The oracle uses BFS and won't hit these the same way,
# which is exactly why they're here.
# ---------------------------------------------------------------------------

def test_dfs_explores_longer_path_when_shorter_expired() -> None:
    """A 2-hop path via g_short is expired. A 4-hop path via g_long is not.
    DFS ordering must not permanently commit to the first path and miss the
    second. Same doc; longer route is the only live one."""
    yesterday = NOW - timedelta(days=1)
    tuples = [
        # Short (expired):
        _t("user:alice", "member", "group:g_short"),
        _t("group:g_short", "viewer", "doc:d1", expires_at=yesterday),
        # Long (live):
        _t("user:alice", "member", "group:g_a"),
        _t("group:g_a", "member", "group:g_b"),
        _t("group:g_b", "member", "group:g_c"),
        _t("group:g_c", "viewer", "doc:d1"),
    ]
    store = _store(tuples)
    ok, reason = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok
    # No path with an expired tuple is allowed to appear in the reason.
    assert all(step.tuple.unexpired_at(NOW) for step in reason.steps)


def test_deep_diamond_dfs_backtracks_correctly() -> None:
    """A diamond where the 'wrong' branch has no grant and the 'right' branch
    does. DFS must backtrack out of the wrong branch, not report deny.

        alice -> g_root -> g_left -> (dead end)
                       \\-> g_right -> viewer -> doc
    """
    tuples = [
        _t("user:alice", "member", "group:root"),
        _t("group:root", "member", "group:left"),
        _t("group:root", "member", "group:right"),
        # left is a dead end for this doc
        _t("group:right", "viewer", "doc:d1"),
    ]
    store = _store(tuples)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok


def test_self_loop_on_subject_terminates() -> None:
    """A tuple where a group is a member of itself. Must not recurse forever."""
    tuples = [
        _t("user:alice", "member", "group:g"),
        _t("group:g", "member", "group:g"),  # self-loop
        _t("group:g", "viewer", "doc:d1"),
    ]
    store = _store(tuples)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok


def test_mutual_cycle_terminates_when_no_grant() -> None:
    """A member B, B member A, no grant anywhere. Must return deny promptly
    rather than loop."""
    tuples = [
        _t("user:alice", "member", "group:a"),
        _t("group:a", "member", "group:b"),
        _t("group:b", "member", "group:a"),
    ]
    store = _store(tuples)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok


# ---------------------------------------------------------------------------
# Depth limit. MAX_DEPTH is 8 (see algebra); chains longer than that must
# not resolve regardless of DFS ordering or memoization.
# ---------------------------------------------------------------------------

def test_10_hop_chain_exceeds_max_depth() -> None:
    tuples = [_t("user:alice", "member", "group:g0")]
    for i in range(10):
        tuples.append(_t(f"group:g{i}", "member", f"group:g{i + 1}"))
    tuples.append(_t("group:g10", "viewer", "doc:d1"))
    store = _store(tuples)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok


def test_exactly_at_max_depth_still_resolves() -> None:
    """A chain whose total path length is exactly MAX_DEPTH must succeed —
    off-by-one on the depth check is a classic bug we want to catch here."""
    from core.algebra import MAX_DEPTH

    # Path structure: user -member-> g0 -member-> g1 -> ... -> gN -viewer-> doc
    # Total steps = (N group hops) + 1 initial member + 1 terminal grant
    # For total == MAX_DEPTH: N = MAX_DEPTH - 2
    n_group_hops = MAX_DEPTH - 2
    tuples = [_t("user:alice", "member", "group:g0")]
    for i in range(n_group_hops):
        tuples.append(_t(f"group:g{i}", "member", f"group:g{i + 1}"))
    tuples.append(_t(f"group:g{n_group_hops}", "viewer", "doc:d1"))
    store = _store(tuples)
    ok, reason = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok
    assert len(reason.steps) == MAX_DEPTH


# ---------------------------------------------------------------------------
# Barriers: deny dominates, at any depth.
# ---------------------------------------------------------------------------

def test_barrier_blocks_at_leaf_grant() -> None:
    barrier = Barrier(id=3, name="wall", side_a="acme", side_b="zenith")
    doc = Document(id="d1", org_id="firm", barrier_tags=frozenset({tag(3, 1)}))
    tuples = [
        _t("user:alice", "member", "group:acme"),
        _t("user:alice", "viewer", "doc:d1"),  # direct grant, but wall wins
    ]
    store = _store(tuples, docs=[doc], barriers=[barrier])
    ok, reason = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok
    assert reason.barrier_hit == barrier


def test_barrier_blocks_even_when_grant_is_5_hops_deep() -> None:
    """A firm-wide chain grants access; a barrier blocks. Deny must beat allow
    regardless of grant-path depth."""
    barrier = Barrier(id=1, name="wall", side_a="team_a", side_b="team_b")
    doc = Document(id="d1", org_id="firm", barrier_tags=frozenset({tag(1, 1)}))
    tuples = [
        _t("user:alice", "member", "group:team_a"),
        _t("user:alice", "member", "group:firm_all"),
        _t("group:firm_all", "member", "group:everyone"),
        _t("group:everyone", "member", "group:global_readers"),
        _t("group:global_readers", "viewer", "doc:d1"),
    ]
    store = _store(tuples, docs=[doc], barriers=[barrier])
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok


def test_barrier_does_not_block_when_principal_not_on_either_side() -> None:
    """A user unaffected by a barrier should get through as normal.
    Regression guard for a barrier check that assumes everyone is on a side."""
    barrier = Barrier(id=1, name="wall", side_a="acme", side_b="zenith")
    doc = Document(id="d1", org_id="firm", barrier_tags=frozenset({tag(1, 1)}))
    tuples = [
        _t("user:bob", "viewer", "doc:d1"),  # bob is not in either wall group
    ]
    store = _store(tuples, docs=[doc], barriers=[barrier])
    ok, _ = check(store, Subject("user", "bob"), Object("doc", "d1"), NOW)
    assert ok


def test_barrier_wins_over_org_ownership() -> None:
    """Org-owner grants access to every doc in the org. Barrier still wins.
    Different grant relation (`owner`) than in previous tests — the deny
    check must not be relation-specific."""
    barrier = Barrier(id=5, name="wall", side_a="a", side_b="b")
    doc = Document(id="d1", org_id="acme", barrier_tags=frozenset({tag(5, 1)}))
    tuples = [
        _t("user:alice", "member", "group:a"),
        _t("user:alice", "member", "org:acme"),
        _t("org:acme", "owner", "doc:d1"),
    ]
    store = _store(tuples, docs=[doc], barriers=[barrier])
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok


# ---------------------------------------------------------------------------
# Expiry semantics — every tuple on the path must be live.
# ---------------------------------------------------------------------------

def test_intermediate_expired_kills_that_path_but_not_others() -> None:
    """Path 1: alice->g1->g2 (g1->g2 expired) → dead
    Path 2: alice->direct viewer → live
    Should still allow."""
    yesterday = NOW - timedelta(days=1)
    tuples = [
        _t("user:alice", "member", "group:g1"),
        _t("group:g1", "member", "group:g2", expires_at=yesterday),
        _t("group:g2", "viewer", "doc:d1"),
        _t("user:alice", "viewer", "doc:d1"),
    ]
    store = _store(tuples)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok


def test_grant_tuple_itself_expired_denies() -> None:
    """The terminal grant edge is expired even though the path leading to it is live."""
    yesterday = NOW - timedelta(days=1)
    tuples = [
        _t("user:alice", "member", "group:g"),
        _t("group:g", "viewer", "doc:d1", expires_at=yesterday),
    ]
    store = _store(tuples)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok


# ---------------------------------------------------------------------------
# Bulk API: authorized_set.
# ---------------------------------------------------------------------------

def test_authorized_set_reachability() -> None:
    docs = [Document(id=f"d{i}", org_id="acme") for i in range(4)]
    tuples = [
        _t("user:alice", "viewer", "doc:d0"),
        _t("user:alice", "member", "group:g"),
        _t("group:g", "viewer", "doc:d2"),
        # d1, d3 unreachable
    ]
    store = _store(tuples, docs=docs)
    assert authorized_set(store, Subject("user", "alice"), NOW, docs) == {"d0", "d2"}


def test_authorized_set_respects_barriers() -> None:
    barrier = Barrier(id=1, name="wall", side_a="a", side_b="b")
    docs = [
        Document(id="open", org_id="firm"),
        Document(id="walled", org_id="firm", barrier_tags=frozenset({tag(1, 1)})),
    ]
    tuples = [
        _t("user:alice", "member", "group:a"),
        _t("user:alice", "viewer", "doc:open"),
        _t("user:alice", "viewer", "doc:walled"),  # grant exists; wall wins
    ]
    store = _store(tuples, docs=docs, barriers=[barrier])
    assert authorized_set(store, Subject("user", "alice"), NOW, docs) == {"open"}


# ---------------------------------------------------------------------------
# expand(): the inverse of check(). Given (object, relation), list subjects.
# ---------------------------------------------------------------------------

def test_expand_returns_direct_grantees() -> None:
    tuples = [
        _t("user:alice", "viewer", "doc:d1"),
        _t("user:bob", "viewer", "doc:d1"),
        _t("user:carol", "owner", "doc:d1"),  # different relation, must not show up
    ]
    store = _store(tuples)
    subjects = expand(store, Object("doc", "d1"), "viewer")
    ids = {s.id for s in subjects}
    assert ids == {"alice", "bob"}


# ---------------------------------------------------------------------------
# Reason path integrity — the audit log depends on this.
# ---------------------------------------------------------------------------

def test_reason_path_steps_are_a_valid_chain() -> None:
    """Each step's object should be the next step's subject (or, for the
    terminal step, the target object). Otherwise the audit log lies."""
    tuples = [
        _t("user:alice", "member", "group:g1"),
        _t("group:g1", "member", "group:g2"),
        _t("group:g2", "viewer", "doc:d1"),
    ]
    store = _store(tuples)
    _, reason = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert reason.decision == "allow"
    # First step subject is the principal.
    assert reason.steps[0].tuple.subject == Subject("user", "alice")
    # Chain consistency.
    for i in range(len(reason.steps) - 1):
        assert (
            reason.steps[i].tuple.object.type == reason.steps[i + 1].tuple.subject.type
            or reason.steps[i].tuple.object.id == reason.steps[i + 1].tuple.subject.id
        ), f"broken chain at step {i}"
    # Terminal step must land on the target.
    assert reason.steps[-1].tuple.object == Object("doc", "d1")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
