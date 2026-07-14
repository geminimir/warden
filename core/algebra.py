"""
The formal permission algebra.

This module is the single source of truth for what "authorized" means in Warden.
Both the reference oracle (`core/oracle.py`) and the real engine (`core/rebac.py`,
W1) are judged against this spec. If they disagree with the algebra stated here,
they are wrong — not this file.

--------------------------------------------------------------------------------
Definition
--------------------------------------------------------------------------------

    authorized(u, d, t) := allow(u, d, t) AND NOT blocked(u, d)

    allow(u, d, t) := there exists a path from u to d through
                      {member, parent, viewer, owner} tuples,
                      such that:
                          - path length <= MAX_DEPTH
                          - every tuple on the path is unexpired at time t
                            (a tuple is unexpired iff expires_at is None
                             or expires_at > t)

    blocked(u, d) := there exists a barrier b such that
                          u is on side X of b
                      AND d is tagged with (b, Y)
                      AND X != Y

Deny dominates. No grant, at any depth, can override a barrier.

--------------------------------------------------------------------------------
Barrier tag encoding
--------------------------------------------------------------------------------

The encoding is what lets deny checks collapse to a single int-array overlap,
so deny costs nothing more than allow at query time.

    tag(barrier_id, side) = barrier_id * 2 + side          # side in {0=A, 1=B}

    doc.barrier_tags = { tag(b, side_of_the_doc)      for each barrier b the
                                                        doc sits behind }
    B(u)             = { tag(b, OPPOSITE side from u) for each barrier b the
                                                        user is on }

    blocked(u, d)  <=>  doc.barrier_tags INTERSECT B(u) is nonempty

Why "opposite" is on the user side, not the doc side: this reduces deny at
query time to the same primitive as allow (array overlap). It is not a
different semantic; it is the same predicate, restated so an index can serve it.

--------------------------------------------------------------------------------
Design constants
--------------------------------------------------------------------------------

MAX_DEPTH = 8
    Chosen so realistic org structures (firm > practice > matter > space > doc,
    plus a nested group or two) fit with headroom, while guaranteeing every
    check() terminates in bounded time even with adversarial input.
    Cycles are handled by a visited-set in the walker; the depth limit is
    belt-and-suspenders and also caps latency in pathological but non-cyclic
    graphs (e.g. a 10^6-node DAG).

RELATIONS = {"member", "parent", "viewer", "owner"}
    These are the only relations that carry a grant. Any other relation is
    metadata (e.g. "shared_by") and MUST NOT be traversed by allow().

--------------------------------------------------------------------------------
What is NOT in this module
--------------------------------------------------------------------------------

    - Storage. Tuples live in Postgres (W1); this module doesn't know that.
    - Caching. That's W2's Redis layer, and it's a permissive superset of the
      truth defined here, never a replacement for it.
    - Filtering. `LabelFilter(u) is a superset of Authorized(u)` is a W2
      invariant. It is asserted against this module's oracle in W2's property
      tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

MAX_DEPTH = 8

Relation = Literal["member", "parent", "viewer", "owner"]
GRANT_RELATIONS: frozenset[Relation] = frozenset({"member", "parent", "viewer", "owner"})

SubjectType = Literal["user", "group", "org"]
ObjectType = Literal["user", "group", "org", "matter", "space", "doc"]
Side = Literal[0, 1]  # 0 = side A, 1 = side B


@dataclass(frozen=True)
class Subject:
    """A grant-carrying entity: a user, group, or org."""

    type: SubjectType
    id: str

    def __str__(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class Object:
    """Anything a tuple can point at: a doc, or a container (org/matter/space/group)."""

    type: ObjectType
    id: str

    def __str__(self) -> str:
        return f"{self.type}:{self.id}"


@dataclass(frozen=True)
class Tuple:
    """A single ReBAC relationship: subject `relation` object.

    `expires_at is None` means permanent. Time-boxed guest access uses a
    concrete datetime.
    """

    subject: Subject
    relation: Relation
    object: Object
    expires_at: datetime | None = None

    def unexpired_at(self, t: datetime) -> bool:
        return self.expires_at is None or self.expires_at > t

    def __str__(self) -> str:
        suffix = f" [expires {self.expires_at.isoformat()}]" if self.expires_at else ""
        return f"{self.subject} --{self.relation}--> {self.object}{suffix}"


@dataclass(frozen=True)
class Barrier:
    """An information barrier (ethical wall) between two groups.

    A principal belonging to `side_a` is blocked from documents tagged on
    `side_b`, and vice versa. Symmetric.
    """

    id: int
    name: str
    side_a: str  # group id
    side_b: str  # group id


def tag(barrier_id: int, side: Side) -> int:
    """Encode (barrier, side) into a single int for int[] overlap checks.

    Invariant: tags for opposite sides of the same barrier differ by exactly 1
    and never collide with tags of any other barrier.
    """
    return barrier_id * 2 + side


def opposite(side: Side) -> Side:
    return 1 if side == 0 else 0


@dataclass(frozen=True)
class Document:
    """A retrievable document with its authorization annotations.

    `barrier_tags` are precomputed for the doc's side of any barrier it sits
    behind — see the module docstring for why the "opposite" is stored on the
    user side, not here.
    """

    id: str
    org_id: str
    barrier_tags: frozenset[int] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ReasonStep:
    """One tuple in a ReasonPath. `via` explains what role it played."""

    tuple: Tuple
    via: Literal["grant", "deny"]


@dataclass(frozen=True)
class ReasonPath:
    """The chain of tuples that produced a decision, plus the decision itself.

    An empty `steps` list means "no grant path found" (for deny decisions with
    no allow path) or "no path evaluated" (for barrier-only deny — the deny
    tuple is recorded on `barrier_hit` instead).
    """

    decision: Literal["allow", "deny"]
    steps: tuple[ReasonStep, ...] = ()
    barrier_hit: Barrier | None = None

    @classmethod
    def allow(cls, steps: tuple[ReasonStep, ...]) -> ReasonPath:
        return cls(decision="allow", steps=steps)

    @classmethod
    def deny(
        cls,
        steps: tuple[ReasonStep, ...] = (),
        barrier_hit: Barrier | None = None,
    ) -> ReasonPath:
        return cls(decision="deny", steps=steps, barrier_hit=barrier_hit)


@dataclass(frozen=True)
class Graph:
    """The complete authorization state at a point in time.

    Immutable on purpose. Tests construct a new Graph per scenario rather than
    mutating a shared one — this keeps the oracle's semantics trivially clear
    and makes concurrent-access tests in later milestones straightforward.
    """

    tuples: frozenset[Tuple]
    barriers: frozenset[Barrier]
    documents: frozenset[Document]

    def barriers_by_id(self) -> dict[int, Barrier]:
        return {b.id: b for b in self.barriers}
