"""
Document ingestion into pgvector.

    ensure_org_partition(conn, org_id)
        Create the per-org partition if it doesn't exist yet, plus its
        GIN + HNSW indexes. Idempotent. Called before upsert into a new org.

    upsert_document(conn, doc, embedding)
        Insert or update a document row. `doc.acl_labels` should already
        be materialized by the caller (via core.labels.materialize_doc_labels)
        — this module doesn't do the label walk itself, it just persists.

The rest of retrieval is in strategies.py.
"""

from __future__ import annotations

from typing import Sequence

import psycopg

from core.algebra import Document


def ensure_org_partition(conn: psycopg.Connection, org_id: str) -> None:
    """Create the LIST partition for `org_id` plus per-partition indexes.

    Idempotent. Safe to call before every upsert; the CREATE ... IF NOT
    EXISTS guards make repeated calls cheap.

    Postgres does not accept bind parameters in partition-DDL (`FOR VALUES
    IN (...)`), so `org_id` is interpolated as a SQL literal. The
    _validate_partition_id() gate above restricts it to
    `[A-Za-z0-9_]+`, which is safe against injection AND is the same
    charset that appears in the partition table name.
    """
    _validate_partition_id(org_id)
    partition = _partition_name(org_id)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {partition} "
        f"PARTITION OF documents FOR VALUES IN ('{org_id}')"
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS {partition}_acl_gin ON {partition} USING GIN (acl_labels)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS {partition}_barrier_gin ON {partition} USING GIN (barrier_tags)")
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {partition}_embedding_hnsw "
        f"ON {partition} USING hnsw (embedding vector_cosine_ops)"
    )


def upsert_document(
    conn: psycopg.Connection,
    doc: Document,
    acl_labels: Sequence[int],
    embedding: Sequence[float] | None = None,
    content: str | None = None,
    labels_epoch: int = 0,
) -> None:
    """Insert or update a document row.

    `acl_labels` is passed in explicitly (rather than derived) because
    materialization is the caller's responsibility. The store here is
    dumb persistence.
    """
    ensure_org_partition(conn, doc.org_id)
    embedding_str = _vector_literal(embedding) if embedding is not None else None
    conn.execute(
        """
        INSERT INTO documents (id, org_id, acl_labels, barrier_tags, labels_epoch, content, embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id, org_id) DO UPDATE SET
            acl_labels   = EXCLUDED.acl_labels,
            barrier_tags = EXCLUDED.barrier_tags,
            labels_epoch = EXCLUDED.labels_epoch,
            content      = EXCLUDED.content,
            embedding    = EXCLUDED.embedding
        """,
        (
            doc.id,
            doc.org_id,
            list(acl_labels),
            list(doc.barrier_tags),
            labels_epoch,
            content,
            embedding_str,
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALLOWED_ID = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _validate_partition_id(org_id: str) -> None:
    """Belt-and-suspenders: reject org_ids that could inject SQL through the
    partition name interpolation in ensure_org_partition."""
    if not org_id or not all(c in _ALLOWED_ID for c in org_id):
        raise ValueError(
            f"invalid org_id {org_id!r} — must be non-empty, alphanumeric+underscore"
        )


def _partition_name(org_id: str) -> str:
    return f"documents_{org_id}"


def _vector_literal(v: Sequence[float]) -> str:
    """pgvector accepts a text form '[1,2,3]'. Using the text form avoids
    a hard dep on the pgvector Python client. Fine for W2's small corpora."""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"
