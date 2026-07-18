"""
FastAPI surface for the gateway.

Endpoints:

    POST /session/create      {principal_type, principal_id} -> {session_id}
    POST /retrieve            {session_id, candidate_doc_ids} -> {docs[]}
    POST /session/revalidate  {session_id} -> {evicted[]}
    POST /answer/verify       {session_id, citations[]} -> {stripped[]}
    POST /tuples              {op, tuple} -> {ok}

Design notes:

    - The principal is bound to the session at /session/create and is
      **never** a parameter on downstream endpoints. A prompt-injected
      tool call cannot influence WHO is asking, only WHAT they ask for.
    - /retrieve takes `candidate_doc_ids` rather than doing ANN in-line.
      Retrieval strategy (exact vs. iterative vs. partitioned) belongs
      in `retrieval/strategies.py`; this endpoint is Gate 2 only. The
      demo agent calls retrieval directly and then feeds candidates
      here — same as production.
    - Dependencies (store, audit, sessions) are wired via a small app
      factory `create_app(deps)` so tests can inject in-memory
      implementations without touching Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from core.algebra import Object, Subject, Tuple
from core.store import TupleStore
from gateway.audit import AuditWriter
from gateway.gates import (
    gate3_revalidate,
    gate3_verify_citations,
    retrieve_authorized,
)
from gateway.session import SessionNotFound, SessionStore


@dataclass
class GatewayDeps:
    """Everything the API needs, held in one place for injection."""

    store: TupleStore
    audit: AuditWriter
    sessions: SessionStore


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    principal_type: Literal["user", "group", "org"] = "user"
    principal_id: str


class CreateSessionResponse(BaseModel):
    session_id: str


class RetrieveRequest(BaseModel):
    session_id: str
    candidate_doc_ids: list[str] = Field(default_factory=list)


class RetrievedDoc(BaseModel):
    doc_id: str


class RetrieveResponse(BaseModel):
    docs: list[RetrievedDoc]


class RevalidateResponse(BaseModel):
    evicted: list[str]


class VerifyRequest(BaseModel):
    session_id: str
    citations: list[str]


class VerifyResponse(BaseModel):
    stripped: list[str]


class TupleWriteRequest(BaseModel):
    op: Literal["write", "delete"]
    subject_type: str
    subject_id: str
    relation: str
    object_type: str
    object_id: str


class OkResponse(BaseModel):
    ok: bool = True


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(deps: GatewayDeps) -> FastAPI:
    """Build the FastAPI app around a set of dependencies.

    Kept as a factory (not a module-level `app = FastAPI()`) so tests can
    inject in-memory implementations and the eventual W5 docker-compose
    can wire in Postgres + Redis without editing this module.
    """
    app = FastAPI(title="warden-gateway", version="0.3.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/session/create", response_model=CreateSessionResponse)
    def create_session(req: CreateSessionRequest) -> CreateSessionResponse:
        principal = Subject(type=req.principal_type, id=req.principal_id)
        session = deps.sessions.create(principal)
        return CreateSessionResponse(session_id=session.session_id)

    @app.post("/retrieve", response_model=RetrieveResponse)
    def retrieve(req: RetrieveRequest) -> RetrieveResponse:
        try:
            kept = retrieve_authorized(
                deps.store, deps.audit, deps.sessions,
                req.session_id, req.candidate_doc_ids,
            )
        except SessionNotFound as e:
            raise HTTPException(status_code=404, detail=f"session not found: {e}")
        return RetrieveResponse(docs=[RetrievedDoc(doc_id=r.doc_id) for r in kept])

    @app.post("/session/revalidate", response_model=RevalidateResponse)
    def revalidate(session_id: str) -> RevalidateResponse:
        try:
            evicted = gate3_revalidate(deps.store, deps.audit, deps.sessions, session_id)
        except SessionNotFound as e:
            raise HTTPException(status_code=404, detail=f"session not found: {e}")
        return RevalidateResponse(evicted=[r.doc_id for r in evicted])

    @app.post("/answer/verify", response_model=VerifyResponse)
    def verify(req: VerifyRequest) -> VerifyResponse:
        try:
            stripped = gate3_verify_citations(
                deps.store, deps.audit, deps.sessions,
                req.session_id, req.citations,
            )
        except SessionNotFound as e:
            raise HTTPException(status_code=404, detail=f"session not found: {e}")
        return VerifyResponse(stripped=stripped)

    @app.post("/tuples", response_model=OkResponse)
    def tuples(req: TupleWriteRequest) -> OkResponse:
        # Only InMemoryStore has direct mutation; PostgresStore needs a
        # connection and write_tuple helper. For W3 we support the in-
        # memory dev path via a rebuild; production wiring is a W5 concern
        # (the gateway container will hold a live PG connection and route
        # writes through core.postgres_store.write_tuple).
        if not hasattr(deps.store, "_outgoing"):
            raise HTTPException(
                status_code=501,
                detail="tuple write via API is currently in-memory-only; use "
                "core.postgres_store.write_tuple against Postgres directly",
            )
        t = Tuple(
            subject=Subject(type=req.subject_type, id=req.subject_id),  # type: ignore[arg-type]
            relation=req.relation,  # type: ignore[arg-type]
            object=Object(type=req.object_type, id=req.object_id),  # type: ignore[arg-type]
        )
        _rebuild_in_memory_store(deps.store, add=t if req.op == "write" else None,
                                 remove=t if req.op == "delete" else None)
        return OkResponse()

    return app


# ---------------------------------------------------------------------------
# In-memory store hot-patch (dev-only)
# ---------------------------------------------------------------------------

def _rebuild_in_memory_store(store, *, add: Tuple | None = None, remove: Tuple | None = None) -> None:
    """Mutate an InMemoryStore's outgoing dict in place.

    Ordinarily InMemoryStore is immutable; the /tuples endpoint uses this
    escape hatch so tests and demos can revoke access mid-session without
    ceremony. Production writes go through core.postgres_store, not here.
    """
    from collections import defaultdict

    from core.algebra import Graph

    graph: Graph = store._graph  # type: ignore[attr-defined]
    tuples = set(graph.tuples)
    if add is not None:
        tuples.add(add)
    if remove is not None:
        tuples.discard(remove)
    new_graph = Graph(
        tuples=frozenset(tuples),
        barriers=graph.barriers,
        documents=graph.documents,
    )
    # Overwrite in place so the API's `deps.store` reference stays valid.
    store._graph = new_graph
    store._outgoing = defaultdict(list)
    for t in tuples:
        store._outgoing[(t.subject.type, t.subject.id)].append(t)
