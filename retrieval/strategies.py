"""
Three filtered-ANN strategies. All three MUST return correct results
(post-filter equivalence); the interesting differences are recall vs.
selectivity, which W4 benchmarks and plots.

    exact       — flat scan on the filtered set. Correct, degrades linearly.
                  Fallback for small partitions.
    iterative   — pgvector 0.8+ iterative index scan; keep pulling from
                  HNSW until K post-filter matches are found. Primary path.
    partitioned — same query, scoped to a single org partition. Default in
                  production because partition pruning composes with iterative.

Every strategy takes the same inputs and returns the same shape, so a
config flag can switch between them per-request in benchmarks. The engine
(Gate 2) doesn't run in here — the strategies are Gate 1, and their output
is the candidate set that Gate 2 authoritatively re-checks.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Sequence

import psycopg

from retrieval.index import _vector_literal, _partition_name, _validate_partition_id


class Strategy(str, enum.Enum):
    EXACT = "exact"
    ITERATIVE = "iterative"
    PARTITIONED = "partitioned"


@dataclass(frozen=True)
class Candidate:
    id: str
    org_id: str
    similarity: float  # 1 - cosine distance
    acl_labels: list[int]
    barrier_tags: list[int]


def retrieve(
    conn: psycopg.Connection,
    *,
    strategy: Strategy,
    query_embedding: Sequence[float],
    L: set[int],
    B: set[int],
    k: int,
    org_id: str | None = None,
    over_fetch: float = 1.5,
) -> list[Candidate]:
    """Return up to `k` candidates matching the label predicate, ordered by
    similarity to `query_embedding`.

    `over_fetch` is the multiplier the design doc names in §3.1: we pull
    K' = over_fetch * k to absorb Gate 2 evictions from stale-cache doc
    rejections without recall loss.

    All strategies share the same predicate:

        acl_labels && L                     -- at least one grant matches
        AND NOT (barrier_tags && B)         -- no wall blocks
        AND (partition scope if applicable)

    The difference is only in the FROM clause and whether pgvector's
    iterative scan is engaged.
    """
    if strategy == Strategy.EXACT:
        return _retrieve_exact(conn, query_embedding, L, B, k, over_fetch)
    if strategy == Strategy.ITERATIVE:
        return _retrieve_iterative(conn, query_embedding, L, B, k, over_fetch)
    if strategy == Strategy.PARTITIONED:
        if org_id is None:
            raise ValueError("partitioned strategy requires an org_id")
        return _retrieve_partitioned(conn, org_id, query_embedding, L, B, k, over_fetch)
    raise ValueError(f"unknown strategy {strategy!r}")


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def _retrieve_exact(
    conn, query_embedding, L, B, k, over_fetch,
) -> list[Candidate]:
    """Flat scan within the filtered set. No HNSW involvement — correctness
    baseline that all other strategies can be compared against."""
    fetch = max(k, int(k * over_fetch))
    rows = conn.execute(
        """
        SELECT id, org_id, acl_labels, barrier_tags,
               1 - (embedding <=> %s::vector) AS similarity
        FROM documents
        WHERE acl_labels && %s::int[]
          AND NOT (barrier_tags && %s::int[])
          AND embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (
            _vector_literal(query_embedding),
            sorted(L),
            sorted(B),
            _vector_literal(query_embedding),
            fetch,
        ),
    ).fetchall()
    return [_row_to_candidate(r) for r in rows[:k]]


def _retrieve_iterative(
    conn, query_embedding, L, B, k, over_fetch,
) -> list[Candidate]:
    """pgvector iterative scan. Keeps pulling from HNSW until K post-filter
    matches are found. Bounded by hnsw.iterative_scan_max_scan_tuples.
    """
    fetch = max(k, int(k * over_fetch))
    # SET LOCAL only affects this transaction. Requires pgvector 0.8+.
    conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
    rows = conn.execute(
        """
        SELECT id, org_id, acl_labels, barrier_tags,
               1 - (embedding <=> %s::vector) AS similarity
        FROM documents
        WHERE acl_labels && %s::int[]
          AND NOT (barrier_tags && %s::int[])
          AND embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (
            _vector_literal(query_embedding),
            sorted(L),
            sorted(B),
            _vector_literal(query_embedding),
            fetch,
        ),
    ).fetchall()
    return [_row_to_candidate(r) for r in rows[:k]]


def _retrieve_partitioned(
    conn, org_id, query_embedding, L, B, k, over_fetch,
) -> list[Candidate]:
    """Iterative scan restricted to one org's partition. In production this
    is the default: intra-tenant selectivity is far less severe than
    cross-tenant, and partition pruning composes cleanly with iterative
    HNSW.
    """
    _validate_partition_id(org_id)
    fetch = max(k, int(k * over_fetch))
    conn.execute("SET LOCAL hnsw.iterative_scan = strict_order")
    partition = _partition_name(org_id)
    rows = conn.execute(
        f"""
        SELECT id, org_id, acl_labels, barrier_tags,
               1 - (embedding <=> %s::vector) AS similarity
        FROM {partition}
        WHERE acl_labels && %s::int[]
          AND NOT (barrier_tags && %s::int[])
          AND embedding IS NOT NULL
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        (
            _vector_literal(query_embedding),
            sorted(L),
            sorted(B),
            _vector_literal(query_embedding),
            fetch,
        ),
    ).fetchall()
    return [_row_to_candidate(r) for r in rows[:k]]


def _row_to_candidate(row: tuple) -> Candidate:
    return Candidate(
        id=row[0],
        org_id=row[1],
        acl_labels=row[2],
        barrier_tags=row[3],
        similarity=float(row[4]),
    )
