"""
FastAPI surface tests. Uses starlette's TestClient over in-memory backends;
no actual HTTP or Postgres involved. Verifies the API contract only —
authorization semantics live in gates.py and are tested separately.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from core.algebra import Graph, Object, Subject, Tuple
from core.store import InMemoryStore
from gateway.api import GatewayDeps, create_app
from gateway.audit import InMemoryAuditLog
from gateway.session import InMemorySessionStore


def _t(subject: str, relation: str, obj: str) -> Tuple:
    s_type, s_id = subject.split(":", 1)
    o_type, o_id = obj.split(":", 1)
    return Tuple(
        subject=Subject(type=s_type, id=s_id),  # type: ignore[arg-type]
        relation=relation,  # type: ignore[arg-type]
        object=Object(type=o_type, id=o_id),  # type: ignore[arg-type]
    )


@pytest.fixture()
def client() -> TestClient:
    store = InMemoryStore(
        Graph(
            tuples=frozenset({_t("user:alice", "viewer", "doc:d1")}),
            barriers=frozenset(),
            documents=frozenset(),
        )
    )
    deps = GatewayDeps(store=store, audit=InMemoryAuditLog(), sessions=InMemorySessionStore())
    return TestClient(create_app(deps))


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_create_session_and_retrieve(client: TestClient) -> None:
    session_id = client.post(
        "/session/create", json={"principal_id": "alice"}
    ).json()["session_id"]

    resp = client.post(
        "/retrieve",
        json={"session_id": session_id, "candidate_doc_ids": ["d1", "d_unknown"]},
    ).json()
    ids = {d["doc_id"] for d in resp["docs"]}
    assert ids == {"d1"}  # d_unknown has no grant → dropped


def test_retrieve_unknown_session_404(client: TestClient) -> None:
    resp = client.post(
        "/retrieve",
        json={"session_id": "nonsense", "candidate_doc_ids": []},
    )
    assert resp.status_code == 404


def test_revalidate_evicts_after_tuple_delete(client: TestClient) -> None:
    session_id = client.post(
        "/session/create", json={"principal_id": "alice"}
    ).json()["session_id"]
    client.post("/retrieve", json={"session_id": session_id, "candidate_doc_ids": ["d1"]})

    # Revoke alice's grant.
    client.post(
        "/tuples",
        json={
            "op": "delete",
            "subject_type": "user",
            "subject_id": "alice",
            "relation": "viewer",
            "object_type": "doc",
            "object_id": "d1",
        },
    )

    resp = client.post(f"/session/revalidate?session_id={session_id}").json()
    assert resp["evicted"] == ["d1"]


def test_verify_citations_strips_revoked(client: TestClient) -> None:
    session_id = client.post(
        "/session/create", json={"principal_id": "alice"}
    ).json()["session_id"]
    resp = client.post(
        "/answer/verify",
        json={"session_id": session_id, "citations": ["d1", "forbidden"]},
    ).json()
    assert resp["stripped"] == ["forbidden"]  # d1 is granted; forbidden is not
