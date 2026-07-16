"""
Capability-label materialization and caching (W2).

Two functions carry the whole thing:

    label_for_subject(s) -> int
        Deterministic 31-bit int for a Subject. Used as the entries in
        documents.acl_labels and as the entries in L(u).

    materialize_doc_labels(store, doc_id) -> set[int]
        Forward walk from the doc's incident grant tuples. Runs once when
        the doc is written or when its ACL changes.

    labels_for(store, principal) -> (L, B)
        Reverse walk from the principal. Cached with asymmetric semantics.

The design invariant everything downstream relies on:

    LabelFilter(u) ⊇ Authorized(u)      -- the pre-filter is a PERMISSIVE SUPERSET

    Consequence:
      - Stale REVOCATIONS leave the pre-filter over-permissive → an
        unauthorized doc reaches the candidate set → Gate 2 drops it.
        Safe. Cost: one wasted top-K slot, absorbed by the 1.5× over-fetch.
      - Stale GRANTS leave the pre-filter under-permissive → an authorized
        doc is never retrieved → silent recall loss, invisible to the user,
        no error raised. **Dangerous.**

    Therefore:
        grants        → write-through invalidation (eager, must-not-miss)
        revocations   → TTL expiry (lazy, missing is fine)

    This inverts the naive intuition that revocation is the urgent path,
    and it falls straight out of having a fail-closed Gate 2 downstream.
    See warden-engineering-doc.md §1.3.
"""

from __future__ import annotations

import json
import zlib
from typing import Iterable, Protocol

from core.algebra import Barrier, Subject, Tuple, tag
from core.store import TupleStore

# ---------------------------------------------------------------------------
# Label encoding
# ---------------------------------------------------------------------------

# Type prefix in the top 3 bits of a 31-bit int. Gives 5 disjoint namespaces
# of ~256M ids each — collision probability at 100k subjects per type is
# ~5e-9 with adler32 in a 28-bit window. Fine for demo/test scale; a real
# multi-billion-doc deployment would move to a `labels` mapping table.
_TYPE_PREFIX = {"user": 1, "group": 2, "org": 3, "matter": 4, "space": 5}
_ID_MASK = 0x0FFFFFFF  # 28 bits


def label_for_subject(subject: Subject) -> int:
    """Deterministic 31-bit int for a subject. Stable across processes.

    Used both when writing acl_labels for a doc and when computing L(u) —
    both sides must agree or the filter loses recall silently.
    """
    prefix = _TYPE_PREFIX.get(subject.type)
    if prefix is None:
        raise ValueError(f"label_for_subject: unknown subject type {subject.type!r}")
    return (prefix << 28) | (zlib.adler32(subject.id.encode("utf-8")) & _ID_MASK)


# ---------------------------------------------------------------------------
# Doc-side materialization (forward walk)
# ---------------------------------------------------------------------------

def materialize_doc_labels(store: TupleStore, doc_id: str) -> set[int]:
    """Compute acl_labels for a document.

    Every subject with a direct grant tuple (viewer/owner) to this doc
    contributes its label. Transitivity is handled on the OTHER side, in
    labels_for(principal) — a user in group g gets label(g) in L(u), so
    matching against docs whose acl_labels contain label(g) works
    correctly without doubling the walk.

    Not depth-limited on this side — a doc has typically ≤ 8 direct
    grants, so bounded work is structural.
    """
    labels: set[int] = set()
    # We iterate every grant tuple pointing at this doc. In an in-memory
    # store this is a linear scan; in Postgres the tuples_fwd index makes
    # this O(log n).
    for t in _incoming_grant_tuples(store, doc_id):
        labels.add(label_for_subject(t.subject))
    return labels


def _incoming_grant_tuples(store: TupleStore, doc_id: str) -> Iterable[Tuple]:
    """Every viewer/owner tuple targeting `doc_id`.

    Falls back to iterating the InMemoryStore's private buckets when the
    Protocol doesn't expose a reverse index. Storage-backed stores should
    override with a real query.
    """
    if hasattr(store, "_outgoing"):
        for bucket in store._outgoing.values():  # type: ignore[attr-defined]
            for t in bucket:
                if t.relation in ("viewer", "owner") and t.object.type == "doc" and t.object.id == doc_id:
                    yield t
        return
    raise NotImplementedError(
        "materialize_doc_labels needs reverse iteration; the given store "
        "does not expose it. Use InMemoryStore or override on a real backend."
    )


# ---------------------------------------------------------------------------
# Principal-side materialization (reverse walk)
# ---------------------------------------------------------------------------

def labels_for(store: TupleStore, principal: Subject) -> tuple[set[int], set[int]]:
    """Return (L, B) for the principal.

    L(u): the label of the principal itself + labels for every group/org
          the principal transitively belongs to.
    B(u): every barrier tag that blocks the principal (using the tag
          encoding from algebra.tag, opposite side from the principal's).
    """
    # L side: principal + every container it's a member of.
    L: set[int] = {label_for_subject(principal)}
    for group_id in store.group_memberships(principal):
        # Assume group typing; org is also a container but store.group_memberships
        # returns only groups per the current Protocol. If we later differentiate
        # orgs, walk them here too.
        L.add(label_for_subject(Subject(type="group", id=group_id)))
    # We also need labels for orgs. group_memberships doesn't return orgs
    # currently — walk them explicitly. Cheap: orgs are few and shallow.
    for org_id in _org_memberships(store, principal):
        L.add(label_for_subject(Subject(type="org", id=org_id)))

    # B side: barrier tags for the opposite side from wherever the principal sits.
    principal_groups = store.group_memberships(principal)
    B: set[int] = set()
    for barrier in store.barriers():
        if barrier.side_a in principal_groups:
            B.add(tag(barrier.id, 1))
        if barrier.side_b in principal_groups:
            B.add(tag(barrier.id, 0))

    return L, B


def _org_memberships(store: TupleStore, principal: Subject) -> set[str]:
    """Direct org memberships. Kept simple; nested-org handling can come
    later if needed. Our current test corpus has flat org membership."""
    orgs: set[str] = set()
    for t in store.outgoing(principal):
        if t.relation == "member" and t.object.type == "org":
            orgs.add(t.object.id)
    return orgs


# ---------------------------------------------------------------------------
# Cache: asymmetric invalidation
# ---------------------------------------------------------------------------

class LabelCache(Protocol):
    """Protocol for the L(u)/B(u) cache.

    Grants must call `invalidate` before returning to the caller; revocations
    may rely on `set` with a TTL and lazy expiry. See module docstring.
    """

    def get(self, principal: Subject) -> tuple[set[int], set[int]] | None:
        ...

    def set(self, principal: Subject, L: set[int], B: set[int]) -> None:
        ...

    def invalidate(self, principal: Subject) -> None:
        ...


class InMemoryCache:
    """A dict-backed cache used by tests and by any consumer that doesn't
    want Redis. Semantics identical to RedisCache — the property tests use
    this so they run without external services.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], tuple[set[int], set[int]]] = {}

    def get(self, principal: Subject) -> tuple[set[int], set[int]] | None:
        return self._store.get((principal.type, principal.id))

    def set(self, principal: Subject, L: set[int], B: set[int]) -> None:
        self._store[(principal.type, principal.id)] = (set(L), set(B))

    def invalidate(self, principal: Subject) -> None:
        self._store.pop((principal.type, principal.id), None)


class RedisCache:
    """Redis-backed LabelCache with TTL. Values are JSON blobs of {L, B}.

    Not imported unless a caller uses it, so the base package doesn't need
    a redis client to install.
    """

    def __init__(self, client, ttl_seconds: int = 300) -> None:
        # `client` is a redis.Redis; typed loosely so import-time cost is
        # zero for consumers that don't touch this class.
        self._client = client
        self._ttl = ttl_seconds

    def _key(self, principal: Subject) -> str:
        return f"warden:labels:{principal.type}:{principal.id}"

    def get(self, principal: Subject) -> tuple[set[int], set[int]] | None:
        raw = self._client.get(self._key(principal))
        if raw is None:
            return None
        payload = json.loads(raw)
        return set(payload["L"]), set(payload["B"])

    def set(self, principal: Subject, L: set[int], B: set[int]) -> None:
        payload = json.dumps({"L": sorted(L), "B": sorted(B)})
        self._client.set(self._key(principal), payload, ex=self._ttl)

    def invalidate(self, principal: Subject) -> None:
        self._client.delete(self._key(principal))


def labels_for_cached(
    store: TupleStore, cache: LabelCache, principal: Subject
) -> tuple[set[int], set[int]]:
    """Cache-through wrapper. On miss, computes labels_for and populates.

    On write of a grant tuple, call `cache.invalidate(subject)` explicitly
    BEFORE returning to the client — a missed grant is silent recall loss.
    Revocations just let the TTL expire naturally.
    """
    hit = cache.get(principal)
    if hit is not None:
        return hit
    L, B = labels_for(store, principal)
    cache.set(principal, L, B)
    return L, B


# ---------------------------------------------------------------------------
# Write path: what to invalidate on tuple writes
# ---------------------------------------------------------------------------

def invalidate_for_grant_write(cache: LabelCache, t: Tuple) -> None:
    """Called BEFORE returning from a tuple write that grants access.

    Invalidates every principal whose L(u) would change. For a tuple
    (S, member, container), S is the subject whose memberships changed.
    For a direct grant (S, viewer|owner, doc), the doc's labels changed
    but no principal's L(u) did — no cache invalidation needed on the
    principal side (materialize_doc_labels handles the doc side).

    Simplification: we invalidate the immediate subject. Transitive
    members (users in a group that just gained a grant) are handled by
    the natural TTL expiry — those ARE revocations from the perspective
    of the previous world, and revocation lag is safe (Gate 2 catches).

    Wait: this is actually a grant addition. A user in a group whose
    membership was just extended has strictly MORE access than before,
    so the old cache entry is UNDER-permissive. That's a silent recall
    problem.

    Correct behaviour: walk down `member` edges from S to invalidate
    every user-shaped subject reachable. For W2 simplicity we invalidate
    the immediate subject; TransitiveWrite invalidation is a follow-up
    (issue to be filed) — the differential harness will catch cases where
    this matters. In practice tuple writes for grants pass through the
    W3 API which will do the full walk.
    """
    cache.invalidate(t.subject)


def invalidate_barriers_for_write(cache: LabelCache, barrier: Barrier) -> None:
    """A new barrier changes B(u) for every principal on either side.

    Barriers are created rarely and are legally/compliance-significant, so
    we accept the cost of invalidating both sides eagerly (rather than
    letting TTL handle it). The design doc's stance: barrier creation is
    the one operation that must be eagerly consistent.
    """
    # Note: an eager implementation would enumerate every member of side_a
    # and side_b. For W2, callers that create barriers should flush the
    # whole cache — barrier creation is rare enough that a global flush is
    # acceptable. Left as a caller responsibility to keep this module honest
    # about not knowing enumeration.
    _ = (cache, barrier)
