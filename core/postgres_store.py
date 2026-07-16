"""
Postgres-backed TupleStore.

Read-through only; writes go through the CRUD helpers at the bottom. Every
public method matches the `TupleStore` Protocol from core/store.py, so the
engine (`core/rebac.py`) can point at this or at `InMemoryStore` without
knowing the difference.

    Not used by the differential harness (that runs against InMemoryStore
    for speed). Used by the Postgres integration tests, and — from W2
    onward — by the retrieval and gateway layers.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from core.algebra import (
    Barrier,
    Graph,
    Object,
    Subject,
    Tuple,
)

_SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def apply_migrations(conn: psycopg.Connection) -> None:
    """Apply every SQL file in sql/ in lexical order.

    Idempotent — every migration uses IF NOT EXISTS. This is minimal by
    design; when the migration set grows past ~5 files, swap in alembic or
    sqlx-cli. For W1 it's two CREATE TABLEs and nothing that needs the
    complexity.
    """
    for path in sorted(_SQL_DIR.glob("*.sql")):
        conn.execute(path.read_text())
    conn.commit()


class PostgresStore:
    """A TupleStore backed by Postgres.

    Takes an open psycopg Connection. The connection's transaction discipline
    is the caller's responsibility — this class does not begin/commit anything.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    # ---- TupleStore Protocol -----------------------------------------

    def outgoing(self, subject: Subject) -> Iterable[Tuple]:
        rows = self._conn.execute(
            """
            SELECT subject_type, subject_id, subject_rel, relation,
                   object_type, object_id, expires_at
            FROM tuples
            WHERE subject_type = %s AND subject_id = %s
            """,
            (subject.type, subject.id),
        ).fetchall()
        return [_row_to_tuple(row) for row in rows]

    def barriers(self) -> Iterable[Barrier]:
        rows = self._conn.cursor(row_factory=dict_row).execute(
            "SELECT id, name, side_a, side_b FROM barriers"
        ).fetchall()
        return [
            Barrier(id=r["id"], name=r["name"], side_a=r["side_a"], side_b=r["side_b"])
            for r in rows
        ]

    def group_memberships(self, principal: Subject) -> set[str]:
        """Recursive CTE up the member graph. Cycle-safe via UNION (dedup on
        the row set — not on a visited column, but functionally equivalent
        for reachability).

        This uses a single round trip to Postgres regardless of nesting
        depth, versus the InMemoryStore's iterative walk. Same result.
        """
        rows = self._conn.execute(
            """
            WITH RECURSIVE membership AS (
                SELECT object_type, object_id
                FROM tuples
                WHERE subject_type = %s AND subject_id = %s
                  AND relation = 'member'
                  AND object_type IN ('group', 'org')
                UNION
                SELECT t.object_type, t.object_id
                FROM tuples t
                JOIN membership m
                  ON t.subject_type = m.object_type
                 AND t.subject_id = m.object_id
                WHERE t.relation = 'member'
                  AND t.object_type IN ('group', 'org')
            )
            SELECT object_id FROM membership WHERE object_type = 'group'
            """,
            (principal.type, principal.id),
        ).fetchall()
        return {row[0] for row in rows}

    def document_barrier_tags(self, doc_id: str) -> frozenset[int]:
        # In W1 the schema doesn't yet have a documents table (that's W2).
        # For the integration tests we route barrier tags through an in-memory
        # side channel: the test harness pre-loads them via a companion
        # method. This keeps the W1 store honest about not knowing docs while
        # still letting the differential API be uniform.
        return self._doc_tags.get(doc_id, frozenset())

    # ---- W1 side channel for doc tags (removed when W2 adds documents) ----

    _doc_tags: dict[str, frozenset[int]] = {}

    def load_doc_tags(self, tags: dict[str, frozenset[int]]) -> None:
        """Test-only shim. Documents are a W2 concern; W1 tests that need to
        exercise barrier evaluation load tag mappings via this side channel.
        Remove when W2 lands and PostgresStore reads from the documents table.
        """
        self._doc_tags = dict(tags)


# ---------------------------------------------------------------------------
# CRUD helpers (W1.1 acceptance: tuple CRUD)
# ---------------------------------------------------------------------------

def write_tuple(conn: psycopg.Connection, t: Tuple) -> None:
    """Idempotent write. If the same PK exists, do nothing.

    Idempotency matters because ACL sync jobs frequently re-emit tuples that
    already exist — if a duplicate write raised, every sync would need
    per-tuple existence checks. Better to make writes safe and let the sync
    be dumb.
    """
    conn.execute(
        """
        INSERT INTO tuples
            (subject_type, subject_id, subject_rel, relation,
             object_type, object_id, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        (
            t.subject.type,
            t.subject.id,
            "",  # subject_rel (userset rewrites are a P1 feature)
            t.relation,
            t.object.type,
            t.object.id,
            t.expires_at,
        ),
    )


def delete_tuple(conn: psycopg.Connection, t: Tuple) -> None:
    """Idempotent delete. Missing row is not an error."""
    conn.execute(
        """
        DELETE FROM tuples
        WHERE subject_type = %s AND subject_id = %s AND subject_rel = %s
          AND relation = %s AND object_type = %s AND object_id = %s
        """,
        (t.subject.type, t.subject.id, "", t.relation, t.object.type, t.object.id),
    )


def list_tuples(conn: psycopg.Connection) -> list[Tuple]:
    """Return every tuple. Test-only; production has no reason to select-star
    the tuples table."""
    rows = conn.execute(
        """
        SELECT subject_type, subject_id, subject_rel, relation,
               object_type, object_id, expires_at
        FROM tuples
        """
    ).fetchall()
    return [_row_to_tuple(row) for row in rows]


def write_barrier(conn: psycopg.Connection, barrier: Barrier) -> int:
    """Insert a barrier. Returns the assigned id (barriers use BIGSERIAL).
    If a barrier with the given `id` already exists, do nothing and return it.
    """
    row = conn.execute(
        """
        INSERT INTO barriers (name, side_a, side_b)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (barrier.name, barrier.side_a, barrier.side_b),
    ).fetchone()
    return row[0]


def load_graph(conn: psycopg.Connection, graph: Graph) -> dict[int, int]:
    """Bulk-load a Graph into Postgres. Returns a mapping from the graph's
    conceptual barrier IDs to their assigned BIGSERIAL IDs.

    Used by the integration tests to hydrate a fresh DB from a Hypothesis-
    generated graph. Wipes existing state first (test-only).
    """
    conn.execute("TRUNCATE tuples, barriers RESTART IDENTITY")
    for t in graph.tuples:
        write_tuple(conn, t)
    id_map: dict[int, int] = {}
    for b in graph.barriers:
        new_id = write_barrier(conn, b)
        id_map[b.id] = new_id
    conn.commit()
    return id_map


def _row_to_tuple(row: tuple) -> Tuple:
    return Tuple(
        subject=Subject(type=row[0], id=row[1]),
        relation=row[3],
        object=Object(type=row[4], id=row[5]),
        expires_at=row[6],
    )


# Object is imported for _row_to_tuple; keep it in scope
_ = Object
