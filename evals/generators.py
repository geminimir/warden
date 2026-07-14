"""
Hypothesis strategies that produce authorization graphs designed to break naive
implementations.

Each strategy targets a specific failure mode. Composed together in
`authz_graphs()`, they yield the adversarial input the differential harness
runs against.

    A generator that only produces well-formed happy paths is worse than no
    generator, because it manufactures false confidence. Everything here is
    weighted toward the ugly cases.

Shapes covered (from the W0.3 issue):
    - deep group nesting (5+ hops)
    - cyclic memberships (group A member-of group B member-of group A)
    - a principal on both sides of a barrier
    - spaces shared into spaces
    - tuples expiring mid-test
    - a doc reachable by 3 independent grant paths, one of which is revoked
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import strategies as st

from core.algebra import (
    MAX_DEPTH,
    Barrier,
    Document,
    Graph,
    Object,
    Subject,
    Tuple,
    tag,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Primitive strategies
# ---------------------------------------------------------------------------

user_ids = st.integers(min_value=0, max_value=19).map(lambda i: f"u{i}")
group_ids = st.integers(min_value=0, max_value=9).map(lambda i: f"g{i}")
org_ids = st.integers(min_value=0, max_value=2).map(lambda i: f"o{i}")
matter_ids = st.integers(min_value=0, max_value=4).map(lambda i: f"m{i}")
space_ids = st.integers(min_value=0, max_value=4).map(lambda i: f"s{i}")
doc_ids = st.integers(min_value=0, max_value=29).map(lambda i: f"d{i}")

users = user_ids.map(lambda i: Subject("user", i))
groups = group_ids.map(lambda i: Subject("group", i))
orgs = org_ids.map(lambda i: Subject("org", i))

# `expires_at`: about 70% permanent (None), 15% already expired, 15% future.
_past = NOW - timedelta(days=30)
_future = NOW + timedelta(days=30)
expiry = st.one_of(
    st.just(None),
    st.just(None),
    st.just(None),
    st.just(None),
    st.just(_past),
    st.just(_future),
)


# ---------------------------------------------------------------------------
# Tuple strategies — each one targets an adversarial shape
# ---------------------------------------------------------------------------

@st.composite
def _user_to_group(draw) -> Tuple:
    return Tuple(
        subject=draw(users),
        relation="member",
        object=Object("group", draw(group_ids)),
        expires_at=draw(expiry),
    )


@st.composite
def _group_to_group(draw) -> Tuple:
    """Nested group membership. Generates cycles freely — the algebra promises
    the oracle terminates."""
    a = draw(group_ids)
    b = draw(group_ids)
    return Tuple(
        subject=Subject("group", a),
        relation="member",
        object=Object("group", b),
        expires_at=draw(expiry),
    )


@st.composite
def _group_grants_doc(draw) -> Tuple:
    return Tuple(
        subject=Subject("group", draw(group_ids)),
        relation=draw(st.sampled_from(["viewer", "owner"])),
        object=Object("doc", draw(doc_ids)),
        expires_at=draw(expiry),
    )


@st.composite
def _user_grants_doc(draw) -> Tuple:
    return Tuple(
        subject=draw(users),
        relation=draw(st.sampled_from(["viewer", "owner"])),
        object=Object("doc", draw(doc_ids)),
        expires_at=draw(expiry),
    )


@st.composite
def _user_in_org(draw) -> Tuple:
    return Tuple(
        subject=draw(users),
        relation="member",
        object=Object("org", draw(org_ids)),
        expires_at=draw(expiry),
    )


@st.composite
def _org_owns_doc(draw) -> Tuple:
    return Tuple(
        subject=Subject("org", draw(org_ids)),
        relation="owner",
        object=Object("doc", draw(doc_ids)),
        expires_at=draw(expiry),
    )


# ---------------------------------------------------------------------------
# Barrier strategy — two distinct groups per barrier
# ---------------------------------------------------------------------------

@st.composite
def _barrier(draw, barrier_id: int) -> Barrier:
    side_a = draw(group_ids)
    side_b = draw(group_ids.filter(lambda g: g != side_a))
    return Barrier(id=barrier_id, name=f"b{barrier_id}", side_a=side_a, side_b=side_b)


# ---------------------------------------------------------------------------
# Document strategy — barrier tags drawn from the graph's actual barriers
# ---------------------------------------------------------------------------

@st.composite
def _document(draw, org: str, barrier_ids: list[int]) -> Document:
    """A doc in `org`, optionally tagged behind one or more barriers.

    Half the time: no barrier tags at all (the "open" corpus doc case).
    Otherwise: a small subset of the graph's barriers, each with a random side.
    """
    if not barrier_ids or draw(st.booleans()):
        tags: frozenset[int] = frozenset()
    else:
        picked = draw(st.lists(st.sampled_from(barrier_ids), min_size=1, max_size=2, unique=True))
        tags = frozenset(tag(b, draw(st.sampled_from([0, 1]))) for b in picked)
    return Document(id=draw(doc_ids), org_id=org, barrier_tags=tags)


# ---------------------------------------------------------------------------
# The composed graph strategy
# ---------------------------------------------------------------------------

@st.composite
def authz_graphs(draw) -> Graph:
    """A random authorization graph with adversarial shape bias.

    The min/max sizes are tuned to hit interesting composition — big enough
    that cycles and multi-hop paths actually form, small enough that the
    oracle stays fast (it's O(principals * docs * depth)).
    """
    n_users_in_org = draw(st.integers(min_value=1, max_value=6))
    n_group_edges = draw(st.integers(min_value=1, max_value=15))
    n_grants = draw(st.integers(min_value=1, max_value=15))
    n_direct_grants = draw(st.integers(min_value=0, max_value=5))
    n_barriers = draw(st.integers(min_value=0, max_value=3))
    n_docs = draw(st.integers(min_value=1, max_value=10))

    tuples: set[Tuple] = set()
    for _ in range(n_users_in_org):
        tuples.add(draw(_user_to_group()))
        tuples.add(draw(_user_in_org()))
    for _ in range(n_group_edges):
        tuples.add(draw(_group_to_group()))
    for _ in range(n_grants):
        tuples.add(draw(_group_grants_doc()))
    for _ in range(n_direct_grants):
        tuples.add(draw(_user_grants_doc()))
    # Also an org->doc grant so nested-membership + org-ownership paths exist:
    for _ in range(draw(st.integers(min_value=0, max_value=3))):
        tuples.add(draw(_org_owns_doc()))

    barriers = frozenset(draw(_barrier(i)) for i in range(1, n_barriers + 1))
    barrier_ids = [b.id for b in barriers]

    # Pick one org for the documents. In W2 we'll broaden this to
    # multi-partition scenarios; for W0 the interesting stuff is intra-org.
    org = draw(org_ids)
    documents = frozenset(draw(_document(org, barrier_ids)) for _ in range(n_docs))

    return Graph(tuples=frozenset(tuples), barriers=barriers, documents=documents)


# ---------------------------------------------------------------------------
# Targeted-shape strategies (also exported so tests can use them directly)
# ---------------------------------------------------------------------------

@st.composite
def deep_nesting_graph(draw, min_hops: int = 5) -> Graph:
    """A single grant chain of length `min_hops`+ that ends at a doc.

    Guarantees the fidelity property is exercised: `authorized_set(u)` MUST
    contain the terminal doc.

    The total path length is `hops + 2` (user->g0, then `hops` group hops, then
    the terminal group->doc grant). We cap `hops` at `MAX_DEPTH - 2` so the
    generated shape is always reachable under the algebra's depth limit —
    generating unreachable chains here would be testing the depth guard, which
    is what `test_depth_limit_is_enforced` in the oracle suite does explicitly.
    """
    hops = draw(st.integers(min_value=min_hops, max_value=MAX_DEPTH - 2))
    tuples: list[Tuple] = [
        Tuple(
            subject=Subject("user", "alice"),
            relation="member",
            object=Object("group", "g0"),
        )
    ]
    for i in range(hops):
        tuples.append(
            Tuple(
                subject=Subject("group", f"g{i}"),
                relation="member",
                object=Object("group", f"g{i + 1}"),
            )
        )
    tuples.append(
        Tuple(
            subject=Subject("group", f"g{hops}"),
            relation="viewer",
            object=Object("doc", "target"),
        )
    )
    return Graph(
        tuples=frozenset(tuples),
        barriers=frozenset(),
        documents=frozenset([Document(id="target", org_id="acme")]),
    )


@st.composite
def cyclic_membership_graph(draw) -> Graph:
    """Three groups in a cycle, one with a doc grant. Alice is a member of one
    of them. Termination is what's under test."""
    tuples = [
        Tuple(Subject("user", "alice"), "member", Object("group", "a")),
        Tuple(Subject("group", "a"), "member", Object("group", "b")),
        Tuple(Subject("group", "b"), "member", Object("group", "c")),
        Tuple(Subject("group", "c"), "member", Object("group", "a")),
        Tuple(Subject("group", draw(st.sampled_from(["a", "b", "c"]))), "viewer", Object("doc", "d1")),
    ]
    return Graph(
        tuples=frozenset(tuples),
        barriers=frozenset(),
        documents=frozenset([Document(id="d1", org_id="acme")]),
    )


def both_sides_of_barrier_graph() -> st.SearchStrategy[Graph]:
    """A user in BOTH sides of a barrier. Deny must dominate on either side.

    Not randomized — this is a fixed adversarial shape wrapped as a strategy
    so it composes with `@given()`.
    """
    barrier = Barrier(id=1, name="wall", side_a="team_a", side_b="team_b")
    tuples = [
        Tuple(Subject("user", "mallory"), "member", Object("group", "team_a")),
        Tuple(Subject("user", "mallory"), "member", Object("group", "team_b")),
        Tuple(Subject("user", "mallory"), "viewer", Object("doc", "da")),
        Tuple(Subject("user", "mallory"), "viewer", Object("doc", "db")),
    ]
    docs = [
        Document(id="da", org_id="firm", barrier_tags=frozenset({tag(1, 0)})),
        Document(id="db", org_id="firm", barrier_tags=frozenset({tag(1, 1)})),
    ]
    return st.just(
        Graph(
            tuples=frozenset(tuples),
            barriers=frozenset([barrier]),
            documents=frozenset(docs),
        )
    )


def three_paths_one_revoked_graph() -> st.SearchStrategy[Graph]:
    """A doc reachable by 3 independent grant paths. One path is 'revoked'
    (represented as an already-expired tuple). Fidelity requires the other two
    to still grant.
    """
    # Path 1: direct viewer
    # Path 2: via group membership (this one has an expired hop = revoked)
    # Path 3: via org ownership
    yesterday = NOW - timedelta(days=1)
    tuples = [
        Tuple(Subject("user", "alice"), "viewer", Object("doc", "d1")),
        Tuple(Subject("user", "alice"), "member", Object("group", "g")),
        Tuple(Subject("group", "g"), "viewer", Object("doc", "d1"), expires_at=yesterday),
        Tuple(Subject("user", "alice"), "member", Object("org", "acme")),
        Tuple(Subject("org", "acme"), "owner", Object("doc", "d1")),
    ]
    return st.just(
        Graph(
            tuples=frozenset(tuples),
            barriers=frozenset(),
            documents=frozenset([Document(id="d1", org_id="acme")]),
        )
    )
