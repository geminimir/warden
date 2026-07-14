"""
Brute-force reference oracle for the permission algebra.

This is ground truth. It is deliberately stupid: BFS over the whole tuple
graph, one principal at a time, no memoization, no caching, no clever indexing.
Every optimization is a place a bug can hide.

    DO NOT OPTIMIZE THIS FILE. If it becomes hot in benchmarks, the fix is
    to use the real engine (core/rebac.py, W1) instead. This file exists to
    be *obviously correct*, not fast.

See core/algebra.py for the formal spec this implements.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime
from typing import Iterable

from core.algebra import (
    GRANT_RELATIONS,
    MAX_DEPTH,
    Graph,
    Object,
    ReasonPath,
    ReasonStep,
    Subject,
    Tuple,
    opposite,
    tag,
)


class Oracle:
    """The reference implementation. Compare the real engine to this."""

    def __init__(self, graph: Graph) -> None:
        self.graph = graph

        # Forward adjacency: for each (subject_type, subject_id), the outgoing tuples.
        # Built once at construction because the graph is immutable.
        self._out: dict[tuple[str, str], list[Tuple]] = defaultdict(list)
        for t in graph.tuples:
            self._out[(t.subject.type, t.subject.id)].append(t)

    # ---- public API ----------------------------------------------------

    def check(
        self, principal: Subject, obj: Object, at: datetime
    ) -> tuple[bool, ReasonPath]:
        """Return (allowed, reason) for principal->obj at time `at`.

        Order of evaluation matches the algebra: deny is checked first (via the
        barrier set), and if it fires we don't bother looking for a grant path.
        """
        # Deny first. If a barrier blocks this principal from this object, no
        # amount of grant paths can rescue it.
        blocker = self._first_blocking_barrier(principal, obj)
        if blocker is not None:
            return False, ReasonPath.deny(barrier_hit=blocker)

        # Then allow: does any grant path from principal reach obj in <= MAX_DEPTH?
        path = self._find_grant_path(principal, obj, at)
        if path is not None:
            return True, ReasonPath.allow(path)
        return False, ReasonPath.deny()

    def authorized_set(self, principal: Subject, at: datetime) -> set[str]:
        """Return every doc_id this principal is authorized to read at `at`.

        Used by the differential harness to compare "the set of docs the real
        engine returns" to "the set the oracle says are allowed."
        """
        result: set[str] = set()
        blocked_tags = self._principal_barrier_set(principal)
        for doc in self.graph.documents:
            if doc.barrier_tags & blocked_tags:
                continue  # deny wins
            if self._find_grant_path(principal, Object("doc", doc.id), at) is not None:
                result.add(doc.id)
        return result

    # ---- deny (barriers) -----------------------------------------------

    def _principal_barrier_set(self, principal: Subject) -> set[int]:
        """B(u): the tags that BLOCK this principal.

        Following the algebra: for each barrier where the principal is on one
        side, the tag on the OPPOSITE side goes into B(u).
        """
        by_id = self.graph.barriers_by_id()
        principal_groups = self._groups_containing(principal)

        blocked: set[int] = set()
        for barrier in self.graph.barriers:
            on_side_a = barrier.side_a in principal_groups
            on_side_b = barrier.side_b in principal_groups
            # A principal on BOTH sides is blocked from both — union of tags.
            if on_side_a:
                blocked.add(tag(barrier.id, opposite(0)))
            if on_side_b:
                blocked.add(tag(barrier.id, opposite(1)))
        # by_id is retained for the caller's reason-path lookup; suppress lint.
        del by_id
        return blocked

    def _first_blocking_barrier(self, principal: Subject, obj: Object):
        """Return the specific Barrier that blocks this principal from this obj,
        or None. The oracle needs the actual barrier for reason paths, so we
        can't just do a set overlap here."""
        # Only documents carry barrier_tags in the current model.
        if obj.type != "doc":
            return None
        doc = next((d for d in self.graph.documents if d.id == obj.id), None)
        if doc is None or not doc.barrier_tags:
            return None

        principal_groups = self._groups_containing(principal)
        for barrier in self.graph.barriers:
            if barrier.side_a in principal_groups and tag(barrier.id, 1) in doc.barrier_tags:
                return barrier
            if barrier.side_b in principal_groups and tag(barrier.id, 0) in doc.barrier_tags:
                return barrier
        return None

    def _groups_containing(self, principal: Subject) -> set[str]:
        """All group ids the principal is a transitive member of.

        Cycle-safe: a visited set caps every walk. Not depth-limited because
        barrier evaluation is a set-membership question, not a "does a grant
        exist" question — a principal 20 hops deep in group nesting is still
        on the wall's side. Only *grant* paths are depth-limited (see the
        algebra doc for why: MAX_DEPTH exists to cap latency and give
        adversarial-input termination, not to define the semantic).
        """
        seen: set[tuple[str, str]] = set()
        groups: set[str] = set()
        frontier: deque[Subject] = deque([principal])
        while frontier:
            current = frontier.popleft()
            key = (current.type, current.id)
            if key in seen:
                continue
            seen.add(key)
            if current.type == "group":
                groups.add(current.id)
            for t in self._out.get(key, []):
                if t.relation != "member":
                    continue
                if t.object.type not in ("group", "org"):
                    continue
                # Note: `member` walks *up* into containers regardless of expiry
                # for barrier purposes. Expiry gates GRANT paths (see the
                # algebra), and barriers are structural — a lawyer whose team
                # membership technically expired at 5:01pm is still on the
                # wrong side of the wall until membership is actually removed.
                frontier.append(Subject(type="group", id=t.object.id))
        return groups

    # ---- allow (grant paths) -------------------------------------------

    def _find_grant_path(
        self, principal: Subject, obj: Object, at: datetime
    ) -> tuple[ReasonStep, ...] | None:
        """BFS for the shortest grant path from principal to obj.

        Depth-limited by MAX_DEPTH. Cycle-safe via a visited set on
        (subject_type, subject_id). Only tuples with `relation in
        GRANT_RELATIONS` are traversed; anything else is metadata.

        Returns the chain of ReasonSteps if reachable, else None.
        """
        # BFS frontier: (subject, path-so-far).
        start_key = (principal.type, principal.id)
        frontier: deque[tuple[Subject, tuple[ReasonStep, ...]]] = deque([(principal, ())])
        # Visited on subjects, NOT on (subject, path). Different paths to the
        # same subject cannot reach a doc the first path couldn't (the graph
        # is monotone once you're at a given subject), and this is what makes
        # cycles terminate.
        visited: set[tuple[str, str]] = {start_key}

        while frontier:
            current, path = frontier.popleft()
            if len(path) >= MAX_DEPTH:
                continue
            for t in self._out.get((current.type, current.id), []):
                if t.relation not in GRANT_RELATIONS:
                    continue
                if not t.unexpired_at(at):
                    continue  # this hop is dead; every path through it is dead
                next_path = (*path, ReasonStep(tuple=t, via="grant"))
                # Did we land on the target object?
                if t.object.type == obj.type and t.object.id == obj.id:
                    return next_path
                # Otherwise, treat the object as a subject for the next hop:
                # `parent` walks doc->matter->space->org, `member` walks
                # user->group->group, `viewer`/`owner` land on the doc/matter
                # itself and can be re-entered as subjects too (e.g. a group
                # that is `viewer` on a matter grants viewer on its children).
                if t.object.type in ("group", "org", "matter", "space"):
                    next_subject = Subject(type=_object_type_as_subject(t.object.type), id=t.object.id)
                    key = (next_subject.type, next_subject.id)
                    if key not in visited:
                        visited.add(key)
                        frontier.append((next_subject, next_path))
        return None


def _object_type_as_subject(obj_type: str) -> str:
    """Coerce an object type into the subject_type it acts as when we walk
    back into the graph. Groups and orgs are already subject-shaped; matters
    and spaces show up on the object side of `parent` tuples and are treated
    as groups for traversal purposes (a matter's grants apply to everything
    inside it).
    """
    if obj_type in ("group", "org"):
        return obj_type
    # matter/space -> treat as group for the walk. This is a modeling choice
    # documented in algebra.py; alter it there and here in lockstep.
    return "group"


def all_principals(graph: Graph) -> Iterable[Subject]:
    """Every user-type subject that appears anywhere in the graph. Used by the
    differential harness to enumerate 'compare oracle vs. engine for every
    principal'."""
    seen: set[tuple[str, str]] = set()
    for t in graph.tuples:
        if t.subject.type == "user":
            key = (t.subject.type, t.subject.id)
            if key not in seen:
                seen.add(key)
                yield t.subject
