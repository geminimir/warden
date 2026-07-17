"""
Minimal in-memory demo entrypoint.

    make serve
    curl http://localhost:8000/healthz

Boots the FastAPI gateway wired to in-memory backends with a small seeded
corpus (alice can see doc:d1; bob can see doc:d2). Meant for the W5 UI to
plug against and for quick manual smoke tests — not for production, which
would wire Postgres, Redis, and pgvector into GatewayDeps instead.
"""

from __future__ import annotations

from fastapi import FastAPI

from core.algebra import Graph, Object, Subject, Tuple
from core.store import InMemoryStore
from gateway.api import GatewayDeps, create_app
from gateway.audit import InMemoryAuditLog
from gateway.session import InMemorySessionStore


def _seed_store() -> InMemoryStore:
    tuples = [
        Tuple(Subject("user", "alice"), "viewer", Object("doc", "d1")),
        Tuple(Subject("user", "bob"), "viewer", Object("doc", "d2")),
    ]
    return InMemoryStore(
        Graph(tuples=frozenset(tuples), barriers=frozenset(), documents=frozenset())
    )


def app() -> FastAPI:
    """uvicorn --factory entrypoint. Returns a fresh app per boot."""
    deps = GatewayDeps(
        store=_seed_store(),
        audit=InMemoryAuditLog(),
        sessions=InMemorySessionStore(),
    )
    return create_app(deps)
