"""
The real ReBAC engine — check() and expand().

This is the pointy end of Warden. Every retrieval that reaches the LLM was
authorized by a call into this file (in production it happens at Gate 2 of
the gateway, per gateway/gates.py).

Design constraints — all load-bearing:

    1. Different from the oracle. Deliberately.
       The oracle in core/oracle.py uses BFS over a linear-iterated graph.
       This engine uses DFS with an in-progress cycle set, driven by a
       TupleStore Protocol. Different algorithm, different data path,
       different possible bugs. That's what the differential harness is
       designed to catch — if we shared code, a bug in the shared code
       would satisfy both sides and slip through.

    2. Deny first, always.
       Barriers short-circuit before we ever look at grants. This matches
       the algebra (`authorized = allow AND NOT blocked`) and it saves work
       on the common case: if a wall blocks the doc, the whole grant search
       is pointless.

    3. Depth-limited and cycle-safe.
       Depth is capped by MAX_DEPTH (=8, from algebra). Cycles are broken by
       a set of subjects currently on the recursion stack (`in_progress`).
       A visited set for full memoization is trickier because the "would I
       be reachable" answer depends on remaining depth budget, and getting
       that wrong is a fidelity bug (silent recall loss). We skip full
       memoization deliberately — bounded work per call is enough.

    4. Reason path is the return value, not a boolean.
       Every allow decision names the tuples that produced it. Every deny
       decision that hit a barrier names the barrier. This is what makes the
       W3 audit log usable for compliance rather than just retrospective.

See core/algebra.py for the formal spec this implements.
See core/store.py for the TupleStore Protocol.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from core.algebra import (
    GRANT_RELATIONS,
    MAX_DEPTH,
    Barrier,
    Object,
    ReasonPath,
    ReasonStep,
    Subject,
    Tuple,
    tag,
)
from core.store import TupleStore


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check(
    store: TupleStore,
    principal: Subject,
    obj: Object,
    at: datetime,
) -> tuple[bool, ReasonPath]:
    """Return (allowed, reason) for principal->obj at time `at`.

    Order of evaluation is FORMALLY the algebra:
        allow(u, d, t) AND NOT blocked(u, d)

    Order of *evaluation in this function* is the algebra flipped:
        NOT blocked(u, d) — check first, short-circuits
        allow(u, d, t)    — only if not blocked

    That's equivalent, and cheaper: a blocked doc doesn't need a grant search.
    """
    blocker = _first_blocking_barrier(store, principal, obj)
    if blocker is not None:
        return False, ReasonPath.deny(barrier_hit=blocker)

    path = _find_grant_path(store, principal, obj, at)
    if path is not None:
        return True, ReasonPath.allow(path)
    return False, ReasonPath.deny()


def authorized_set(
    store: TupleStore,
    principal: Subject,
    at: datetime,
    documents: Iterable,
) -> set[str]:
    """Return the set of doc_ids this principal is authorized to read.

    Takes the document set as an argument rather than pulling from the store,
    because in production this will be the top-K candidate set from Gate 1,
    not the whole corpus. The store still owns each doc's barrier tags (via
    document_barrier_tags), because those must be authoritative.

    Uses check() rather than duplicating its logic — same code path means
    same semantics, no drift between the point API and the bulk API.
    """
    allowed: set[str] = set()
    for doc in documents:
        ok, _ = check(store, principal, Object("doc", doc.id), at)
        if ok:
            allowed.add(doc.id)
    return allowed


def expand(store: TupleStore, obj: Object, relation: str) -> set[Subject]:
    """Return every subject with a *direct* `relation` tuple on `obj`.

    Note: this is direct, not transitive — expanding transitively is a much
    bigger operation (all users reachable through nested groups) and is what
    W2's `materialize_labels` is for. `expand()` is here so the API is
    Zanzibar-shaped and so tests can inspect the graph.
    """
    result: set[Subject] = set()
    # No cheap reverse index in the base TupleStore Protocol on purpose —
    # storage-backed stores can implement this more efficiently. For the
    # InMemoryStore this ends up being O(n) which is fine at test scale.
    if hasattr(store, "_outgoing"):
        for bucket in store._outgoing.values():  # type: ignore[attr-defined]
            for t in bucket:
                if t.relation == relation and t.object == obj:
                    result.add(t.subject)
    else:
        # Fallback for stores that don't expose bucket iteration: try to walk
        # via `outgoing` for every seen subject. Kept minimal — production
        # implementations will override.
        raise NotImplementedError(
            "expand() requires the store to expose reverse iteration; "
            "use InMemoryStore or override on the storage-backed impl."
        )
    return result


# ---------------------------------------------------------------------------
# Barrier evaluation (deny side)
# ---------------------------------------------------------------------------

def _first_blocking_barrier(
    store: TupleStore, principal: Subject, obj: Object
) -> Barrier | None:
    """Return the specific Barrier that blocks this principal from this obj.

    check() needs the actual Barrier for reason paths, so this can't just do
    a set overlap and drop the identity.
    """
    if obj.type != "doc":
        # Barriers currently only tag documents. Container-level barrier
        # inheritance is a W2 concern (via label materialization).
        return None

    doc_tags = store.document_barrier_tags(obj.id)
    if not doc_tags:
        return None

    principal_groups = store.group_memberships(principal)
    # Walk barriers once. `store.barriers()` is typically small (single-digit
    # to low tens even in large tenants), so we don't index further.
    for barrier in store.barriers():
        if barrier.side_a in principal_groups and tag(barrier.id, 1) in doc_tags:
            return barrier
        if barrier.side_b in principal_groups and tag(barrier.id, 0) in doc_tags:
            return barrier
    return None


# ---------------------------------------------------------------------------
# Grant-path search (allow side)
# ---------------------------------------------------------------------------

def _find_grant_path(
    store: TupleStore,
    principal: Subject,
    obj: Object,
    at: datetime,
) -> tuple[ReasonStep, ...] | None:
    """DFS for a grant path from principal to obj, honouring depth and expiry.

    Returns the sequence of ReasonSteps if reachable within MAX_DEPTH, else
    None. Cycle-safe via `in_progress`: a subject that is currently being
    expanded on the call stack is skipped, so mutual-membership cycles cannot
    cause infinite recursion.

    Not memoized. Adding memoization here would need to key on remaining
    depth budget, and a wrong memo would silently drop reachable docs
    (fidelity bug). Since MAX_DEPTH is 8 and grant_relations is 4, the
    branching factor is bounded and unmemoized DFS is fast enough.
    """
    in_progress: set[tuple[str, str]] = set()
    return _dfs(store, principal, obj, at, MAX_DEPTH, in_progress)


def _dfs(
    store: TupleStore,
    subject: Subject,
    target: Object,
    at: datetime,
    remaining: int,
    in_progress: set[tuple[str, str]],
) -> tuple[ReasonStep, ...] | None:
    if remaining <= 0:
        return None

    key = (subject.type, subject.id)
    if key in in_progress:
        # Cycle detected. Return None so the caller can try an alternative
        # branch. Do NOT mark this as permanently unreachable — a different
        # (u, target, remaining) call may succeed via a different route.
        return None

    in_progress.add(key)
    try:
        for t in store.outgoing(subject):
            if t.relation not in GRANT_RELATIONS:
                continue
            if not t.unexpired_at(at):
                continue

            step = ReasonStep(tuple=t, via="grant")

            # Landed on the target?
            if t.object.type == target.type and t.object.id == target.id:
                return (step,)

            # Otherwise treat the object as a subject and recurse — but only
            # into container-like objects. Landing on a `doc` object that
            # isn't the target is a dead end; you can't traverse *through*
            # a doc.
            if t.object.type in ("group", "org", "matter", "space"):
                next_subject = Subject(type=_as_subject(t.object.type), id=t.object.id)
                sub = _dfs(store, next_subject, target, at, remaining - 1, in_progress)
                if sub is not None:
                    return (step, *sub)
        return None
    finally:
        in_progress.discard(key)


def _as_subject(obj_type: str) -> str:
    """Coerce an object type into the subject_type it acts as when we walk
    back into the graph. Matches the oracle's treatment (see core/oracle.py):
    groups/orgs are subject-shaped; matters/spaces are traversal containers
    treated as groups for the walk.
    """
    if obj_type in ("group", "org"):
        return obj_type
    return "group"
