"""
Sanity tests for the generators themselves.

Each named shape must:
  1. Produce graphs the oracle can process without erroring.
  2. Actually exhibit the shape it claims (e.g., cyclic_membership_graph must
     contain a cycle, deep_nesting_graph must reach depth >= 5).

If the generators lie, the differential harness is lying too.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings

from core.algebra import MAX_DEPTH, Object, Subject
from core.oracle import Oracle
from evals.generators import (
    NOW,
    authz_graphs,
    both_sides_of_barrier_graph,
    cyclic_membership_graph,
    deep_nesting_graph,
    three_paths_one_revoked_graph,
)

_hyp = settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@_hyp
@given(graph=authz_graphs())
def test_random_graphs_dont_crash_oracle(graph) -> None:
    """The primary composed strategy must never produce a graph that breaks
    the oracle. If Hypothesis finds one, either the generator is unrealistic
    or the oracle has a bug — either way we want to know."""
    oracle = Oracle(graph)
    for doc in graph.documents:
        # A representative principal — every generated graph has u0 somewhere
        # in the vicinity via the primitive strategies.
        oracle.check(Subject("user", "u0"), Object("doc", doc.id), NOW)


@_hyp
@given(graph=deep_nesting_graph())
def test_deep_nesting_shape_reaches_target(graph) -> None:
    oracle = Oracle(graph)
    allowed, reason = oracle.check(Subject("user", "alice"), Object("doc", "target"), NOW)
    assert allowed
    assert len(reason.steps) >= 5
    assert len(reason.steps) <= MAX_DEPTH


@_hyp
@given(graph=cyclic_membership_graph())
def test_cyclic_graph_terminates(graph) -> None:
    """The oracle would loop forever if cycles were mishandled; this test
    completing at all is the assertion."""
    oracle = Oracle(graph)
    oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)


@_hyp
@given(graph=both_sides_of_barrier_graph())
def test_both_sides_barrier_blocks_both_docs(graph) -> None:
    oracle = Oracle(graph)
    for doc_id in ("da", "db"):
        allowed, _ = oracle.check(Subject("user", "mallory"), Object("doc", doc_id), NOW)
        assert not allowed


@_hyp
@given(graph=three_paths_one_revoked_graph())
def test_three_paths_one_revoked_still_allows(graph) -> None:
    oracle = Oracle(graph)
    allowed, _ = oracle.check(Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert allowed
