"""
Session store — typed doc-ref sets with provenance.

The whole point of not treating the agent's context as an opaque text blob:
Gate 3 must be able to re-check every document in the context on every model
call. That requires a *typed set of references*, not a blob you'd have to
parse. See warden-design.md §1.6 for why this is Warden's novelty claim.

    A session belongs to exactly one principal (bound server-side at
    /session/create). Everything downstream — the agent's tool calls, the
    LLM's context — refers to it by session_id. The principal is NEVER a
    tool parameter. A prompt-injected document that says "retrieve as user
    zenith-admin" cannot influence who the model is asking as.

Two implementations:
    InMemorySessionStore — a dict of sessions. Fine for W3 tests and
        single-node dev. Sessions are ephemeral anyway.
    (RedisSessionStore lands later if we need multi-node.)
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Protocol

from core.algebra import ReasonPath, Subject


@dataclass(frozen=True)
class DocRef:
    """A reference to a document currently held in an agent's context.

    `granted_by` is the ReasonPath from the check() that let the doc in.
    Preserved so Gate 3's eviction audit rows can reference the ORIGINAL
    grant that has now been revoked.
    """

    doc_id: str
    org_id: str
    added_at: datetime
    granted_by: ReasonPath


@dataclass
class Session:
    session_id: str
    principal: Subject
    created_at: datetime
    doc_refs: list[DocRef] = field(default_factory=list)


class SessionStore(Protocol):
    def create(self, principal: Subject) -> Session: ...
    def get(self, session_id: str) -> Session | None: ...
    def add_refs(self, session_id: str, refs: Iterable[DocRef]) -> None: ...
    def evict_refs(self, session_id: str, doc_ids: Iterable[str]) -> list[DocRef]: ...
    def list_refs(self, session_id: str) -> list[DocRef]: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, principal: Subject) -> Session:
        # 128-bit URL-safe token. Enough entropy that session_id can be
        # treated as unguessable — matters because the principal is bound
        # to it and everything else keys off that binding.
        sid = secrets.token_urlsafe(16)
        session = Session(
            session_id=sid,
            principal=principal,
            created_at=datetime.now(timezone.utc),
        )
        self._sessions[sid] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def add_refs(self, session_id: str, refs: Iterable[DocRef]) -> None:
        session = self._require(session_id)
        # Deduplicate on doc_id — a retrieve call that surfaces a doc already
        # in context shouldn't produce two refs. Keep the earliest added_at
        # so audit reasoning stays consistent.
        existing = {r.doc_id for r in session.doc_refs}
        for r in refs:
            if r.doc_id not in existing:
                session.doc_refs.append(r)
                existing.add(r.doc_id)

    def evict_refs(self, session_id: str, doc_ids: Iterable[str]) -> list[DocRef]:
        session = self._require(session_id)
        doc_ids_set = set(doc_ids)
        evicted = [r for r in session.doc_refs if r.doc_id in doc_ids_set]
        session.doc_refs = [r for r in session.doc_refs if r.doc_id not in doc_ids_set]
        return evicted

    def list_refs(self, session_id: str) -> list[DocRef]:
        session = self._require(session_id)
        # Return a copy — callers shouldn't be able to mutate through this.
        return list(session.doc_refs)

    def _require(self, session_id: str) -> Session:
        s = self._sessions.get(session_id)
        if s is None:
            raise SessionNotFound(session_id)
        return s


class SessionNotFound(KeyError):
    """Raised when a session_id isn't in the store. The gateway API
    translates this into a 404."""
