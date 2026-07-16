"""
Integration tests for PostgresStore.

Only runs when a Postgres URL is provided via WARDEN_TEST_DB_URL. Otherwise
the whole module is skipped so contributors without a running Postgres can
still `make oracle` / `make test` locally.

    Set WARDEN_TEST_DB_URL to something like:
        postgres://warden:warden@localhost:5432/warden

    Or use:
        docker compose up -d postgres
        make postgres-integration
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("psycopg")

import psycopg

from core.algebra import Barrier, Document, Graph, Object, Subject, Tuple, tag
from core.postgres_store import (
    PostgresStore,
    apply_migrations,
    delete_tuple,
    list_tuples,
    load_graph,
    write_tuple,
)
from core.rebac import check

DB_URL = os.environ.get("WARDEN_TEST_DB_URL")
pytestmark = pytest.mark.skipif(
    DB_URL is None,
    reason="Set WARDEN_TEST_DB_URL to run Postgres integration tests. "
    "See docker-compose.yml for a local dev DB.",
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def _migrations() -> None:
    """Apply migrations once per test session."""
    with psycopg.connect(DB_URL) as c:  # type: ignore[arg-type]
        apply_migrations(c)


@pytest.fixture()
def conn(_migrations):
    """Fresh connection per test. Truncates state at the start of each test
    so ordering doesn't matter."""
    c = psycopg.connect(DB_URL)  # type: ignore[arg-type]
    try:
        c.execute("TRUNCATE tuples, barriers RESTART IDENTITY")
        c.commit()
        yield c
    finally:
        c.close()


def _t(subject: str, relation: str, obj: str, expires_at=None) -> Tuple:
    s_type, s_id = subject.split(":", 1)
    o_type, o_id = obj.split(":", 1)
    return Tuple(
        subject=Subject(type=s_type, id=s_id),  # type: ignore[arg-type]
        relation=relation,  # type: ignore[arg-type]
        object=Object(type=o_type, id=o_id),  # type: ignore[arg-type]
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# CRUD (W1.1)
# ---------------------------------------------------------------------------

def test_write_then_list(conn):
    write_tuple(conn, _t("user:alice", "viewer", "doc:d1"))
    write_tuple(conn, _t("user:bob", "member", "group:g1"))
    conn.commit()
    tuples = list_tuples(conn)
    assert len(tuples) == 2


def test_write_is_idempotent(conn):
    t = _t("user:alice", "viewer", "doc:d1")
    write_tuple(conn, t)
    write_tuple(conn, t)  # second write must not raise or duplicate
    conn.commit()
    assert len(list_tuples(conn)) == 1


def test_delete(conn):
    t = _t("user:alice", "viewer", "doc:d1")
    write_tuple(conn, t)
    conn.commit()
    delete_tuple(conn, t)
    conn.commit()
    assert list_tuples(conn) == []


def test_expires_at_roundtrip(conn):
    tomorrow = NOW + timedelta(days=1)
    write_tuple(conn, _t("user:alice", "viewer", "doc:d1", expires_at=tomorrow))
    conn.commit()
    got = list_tuples(conn)[0]
    assert got.expires_at is not None


# ---------------------------------------------------------------------------
# End-to-end: rebac.check() against a Postgres-backed store
# ---------------------------------------------------------------------------

def test_check_against_postgres_direct_grant(conn):
    graph = Graph(
        tuples=frozenset({_t("user:alice", "viewer", "doc:d1")}),
        barriers=frozenset(),
        documents=frozenset({Document(id="d1", org_id="acme")}),
    )
    load_graph(conn, graph)
    store = PostgresStore(conn)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok


def test_check_against_postgres_5_hop_nesting(conn):
    tuples = [_t("user:alice", "member", "group:g0")]
    for i in range(5):
        tuples.append(_t(f"group:g{i}", "member", f"group:g{i + 1}"))
    tuples.append(_t("group:g5", "viewer", "doc:d1"))
    load_graph(
        conn,
        Graph(
            tuples=frozenset(tuples),
            barriers=frozenset(),
            documents=frozenset({Document(id="d1", org_id="acme")}),
        ),
    )
    store = PostgresStore(conn)
    ok, reason = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok
    assert len(reason.steps) == 7


def test_check_against_postgres_with_barrier(conn):
    barrier = Barrier(id=1, name="wall", side_a="team_a", side_b="team_b")
    doc = Document(id="d1", org_id="firm", barrier_tags=frozenset({tag(1, 1)}))
    tuples = [
        _t("user:alice", "member", "group:team_a"),
        _t("user:alice", "viewer", "doc:d1"),  # grant, but wall wins
    ]
    id_map = load_graph(
        conn,
        Graph(
            tuples=frozenset(tuples),
            barriers=frozenset({barrier}),
            documents=frozenset({doc}),
        ),
    )
    store = PostgresStore(conn)
    # Postgres assigned a BIGSERIAL barrier.id which likely differs from
    # our conceptual id=1. Reload doc tags with the mapped id, using the
    # W1 side-channel that PostgresStore exposes for tests.
    real_barrier_id = id_map[1]
    store.load_doc_tags({"d1": frozenset({tag(real_barrier_id, 1)})})
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert not ok
