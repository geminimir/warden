"""
Integration tests for retrieval/ against a real pgvector-backed Postgres.

Skipped without WARDEN_TEST_DB_URL. All three strategies must return
correct results on the same corpus; recall benchmarks land in W4.
"""

from __future__ import annotations

import os
import random

import pytest

pytest.importorskip("psycopg")

import psycopg

from core.algebra import Document, Subject
from core.labels import label_for_subject
from core.postgres_store import apply_migrations
from retrieval.index import upsert_document
from retrieval.strategies import Strategy, retrieve

DB_URL = os.environ.get("WARDEN_TEST_DB_URL")
pytestmark = pytest.mark.skipif(
    DB_URL is None,
    reason="Set WARDEN_TEST_DB_URL to run pgvector integration tests. "
    "See docker-compose.yml for a local dev DB.",
)


@pytest.fixture(scope="session")
def _migrations() -> None:
    with psycopg.connect(DB_URL) as c:  # type: ignore[arg-type]
        apply_migrations(c)


@pytest.fixture()
def conn(_migrations):
    c = psycopg.connect(DB_URL)  # type: ignore[arg-type]
    try:
        # Truncate through the parent — cascades to all partitions.
        c.execute("TRUNCATE documents RESTART IDENTITY CASCADE")
        c.commit()
        yield c
    finally:
        c.close()


def _seed_docs(conn, acl_label: int, org: str = "acme", n: int = 5) -> None:
    """Seed n docs in `org`, all sharing `acl_label`. Embeddings are
    deterministic per-doc so ordering is reproducible."""
    rng = random.Random(42)
    for i in range(n):
        doc = Document(id=f"d{i}", org_id=org)
        emb = [rng.random() for _ in range(768)]
        upsert_document(conn, doc, acl_labels=[acl_label], embedding=emb)
    conn.commit()


def _query_embedding() -> list[float]:
    rng = random.Random(7)
    return [rng.random() for _ in range(768)]


# ---------------------------------------------------------------------------
# Ingestion + partitioning
# ---------------------------------------------------------------------------

def test_upsert_creates_org_partition(conn):
    doc = Document(id="d1", org_id="new_org")
    upsert_document(conn, doc, acl_labels=[42], embedding=[0.1] * 768)
    conn.commit()
    row = conn.execute(
        "SELECT id, org_id, acl_labels FROM documents WHERE id = 'd1'"
    ).fetchone()
    assert row == ("d1", "new_org", [42])


def test_upsert_is_idempotent(conn):
    doc = Document(id="d1", org_id="acme")
    upsert_document(conn, doc, acl_labels=[1], embedding=[0.1] * 768)
    upsert_document(conn, doc, acl_labels=[2], embedding=[0.2] * 768)
    conn.commit()
    row = conn.execute("SELECT acl_labels FROM documents WHERE id = 'd1'").fetchone()
    assert row[0] == [2]  # last write wins


# ---------------------------------------------------------------------------
# All three strategies return the seeded docs when the label matches
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "strategy",
    [Strategy.EXACT, Strategy.ITERATIVE, Strategy.PARTITIONED],
)
def test_retrieve_returns_matching_docs(conn, strategy):
    label = label_for_subject(Subject("group", "readers"))
    _seed_docs(conn, acl_label=label)
    kwargs = {"org_id": "acme"} if strategy == Strategy.PARTITIONED else {}
    results = retrieve(
        conn,
        strategy=strategy,
        query_embedding=_query_embedding(),
        L={label},
        B=set(),
        k=3,
        **kwargs,
    )
    assert len(results) == 3
    assert {r.id for r in results} <= {f"d{i}" for i in range(5)}


@pytest.mark.parametrize(
    "strategy",
    [Strategy.EXACT, Strategy.ITERATIVE, Strategy.PARTITIONED],
)
def test_retrieve_excludes_barrier_blocked_docs(conn, strategy):
    label = label_for_subject(Subject("group", "readers"))
    # Two docs: one open, one behind a barrier tag the query has in B.
    upsert_document(
        conn,
        Document(id="open", org_id="acme"),
        acl_labels=[label],
        embedding=[0.1] * 768,
    )
    upsert_document(
        conn,
        Document(id="walled", org_id="acme", barrier_tags=frozenset({99})),
        acl_labels=[label],
        embedding=[0.11] * 768,
    )
    conn.commit()
    kwargs = {"org_id": "acme"} if strategy == Strategy.PARTITIONED else {}
    results = retrieve(
        conn,
        strategy=strategy,
        query_embedding=_query_embedding(),
        L={label},
        B={99},  # blocks the walled doc
        k=10,
        **kwargs,
    )
    ids = {r.id for r in results}
    assert "open" in ids
    assert "walled" not in ids


def test_retrieve_returns_empty_when_no_label_overlap(conn):
    label = label_for_subject(Subject("group", "readers"))
    _seed_docs(conn, acl_label=label)
    results = retrieve(
        conn,
        strategy=Strategy.EXACT,
        query_embedding=_query_embedding(),
        L={999999},  # user has no matching labels
        B=set(),
        k=10,
    )
    assert results == []


def test_partitioned_scopes_to_org(conn):
    """A partitioned query on org 'acme' must not return docs from other orgs
    even if labels/embeddings would otherwise match."""
    label = label_for_subject(Subject("group", "readers"))
    upsert_document(
        conn,
        Document(id="acme_doc", org_id="acme"),
        acl_labels=[label],
        embedding=[0.1] * 768,
    )
    upsert_document(
        conn,
        Document(id="zenith_doc", org_id="zenith"),
        acl_labels=[label],
        embedding=[0.1] * 768,
    )
    conn.commit()
    results = retrieve(
        conn,
        strategy=Strategy.PARTITIONED,
        query_embedding=_query_embedding(),
        L={label},
        B=set(),
        k=10,
        org_id="acme",
    )
    ids = {r.id for r in results}
    assert "acme_doc" in ids
    assert "zenith_doc" not in ids
