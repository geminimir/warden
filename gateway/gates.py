"""
The three gates, wired end to end.

    Gate 1 · PRE-FILTER
        L(u), B(u) from labels_for. ANN + label-overlap predicate. Over-
        fetch K' = 1.5·K. Best-effort; may be stale.

    Gate 2 · AUTHORITATIVE CHECK   ← the security boundary
        For each candidate: rebac.check() against the tuple graph.
        Fail-closed. Deny > allow. Every decision — allow AND deny —
        writes an audit row.

    Gate 3 · SESSION REVALIDATION
        Before every model call: re-check every doc ref in the session's
        context. Evict revoked. Before the final answer: re-check every
        citation. Strip revoked.

The invariant to memorize:

    Gate 1 is an optimization; Gate 2 is the boundary.
    Gate 1 exists so Gate 2 is cheap. Gate 2 exists so Gate 1 can be wrong.

Every function here is small on purpose. The three gates are the ENTIRE
security posture — anything clever here is a place a bug can hide.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.algebra import Object, ReasonPath, Subject
from core.labels import labels_for
from core.rebac import check
from core.store import TupleStore
from gateway.audit import AuditEntry, AuditWriter
from gateway.session import DocRef, SessionNotFound, SessionStore


def _reason_to_dict(reason: ReasonPath) -> dict[str, Any]:
    """Serialize a ReasonPath for the audit log's jsonb column."""
    return {
        "decision": reason.decision,
        "steps": [
            {
                "subject": str(s.tuple.subject),
                "relation": s.tuple.relation,
                "object": str(s.tuple.object),
                "via": s.via,
            }
            for s in reason.steps
        ],
        "barrier_hit": (
            {"id": reason.barrier_hit.id, "name": reason.barrier_hit.name}
            if reason.barrier_hit
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Gate 2 — authoritative check + audit
# ---------------------------------------------------------------------------

def gate2_check(
    store: TupleStore,
    audit: AuditWriter,
    principal: Subject,
    doc_id: str,
    at: datetime | None = None,
    *,
    action: str,
    gate: int,
    session_id: str | None,
) -> tuple[bool, ReasonPath]:
    """Run the authoritative check and record the decision.

    Every entry through Gate 2 or Gate 3 comes through here so the audit
    log stays uniform. `action` tells the log what the caller was doing
    (retrieve vs. context_hold vs. cite); `gate` distinguishes 2 from 3.
    """
    at = at or datetime.now(timezone.utc)
    ok, reason = check(store, principal, Object("doc", doc_id), at)
    audit.append(
        AuditEntry(
            principal=str(principal),
            object_id=doc_id,
            action=action,
            decision="allow" if ok else "deny",
            reason_path=_reason_to_dict(reason),
            gate=gate,
            session_id=session_id,
        )
    )
    return ok, reason


# ---------------------------------------------------------------------------
# Full retrieve pipeline: Gate 1 → Gate 2 → session.add_refs
# ---------------------------------------------------------------------------

def retrieve_authorized(
    store: TupleStore,
    audit: AuditWriter,
    sessions: SessionStore,
    session_id: str,
    candidate_doc_ids: list[str],
    at: datetime | None = None,
) -> list[DocRef]:
    """Gate 2 half of the retrieve pipeline: take a pre-filtered candidate
    list, authoritatively check each, add allowed ones to the session
    context, audit both allows and denies.

    Gate 1 (label pushdown + ANN) runs OUTSIDE this function — see
    retrieval/strategies.retrieve. We separate them because Gate 1's
    natural home is retrieval/ (it needs pgvector) and Gate 2's is here
    (it needs the audit writer and session store). The design invariant
    holds only when both are called in order; that ordering is enforced
    by the FastAPI /retrieve endpoint.
    """
    session = sessions.get(session_id)
    if session is None:
        raise SessionNotFound(session_id)

    at = at or datetime.now(timezone.utc)
    kept: list[DocRef] = []
    for doc_id in candidate_doc_ids:
        ok, reason = gate2_check(
            store, audit, session.principal, doc_id, at,
            action="retrieve", gate=2, session_id=session_id,
        )
        if ok:
            kept.append(
                DocRef(
                    doc_id=doc_id,
                    org_id="",  # populated by callers who know it; not load-bearing here
                    added_at=at,
                    granted_by=reason,
                )
            )
    sessions.add_refs(session_id, kept)
    return kept


# ---------------------------------------------------------------------------
# Gate 3 — context revalidation + citation verification
# ---------------------------------------------------------------------------

def gate3_revalidate(
    store: TupleStore,
    audit: AuditWriter,
    sessions: SessionStore,
    session_id: str,
    at: datetime | None = None,
) -> list[DocRef]:
    """Re-check every doc ref in the session's context. Evict any that no
    longer authorize. Returns the list of evicted refs.

    Called BEFORE every model call in the agent loop. Cost is O(|context
    refs|) authoritative checks — tens of sub-millisecond checks. Free.

    Every evicted ref writes an audit row with action=evict, gate=3, so
    Gate 3 firings are visible in the compliance log alongside the
    original Gate 2 allow.
    """
    session = sessions.get(session_id)
    if session is None:
        raise SessionNotFound(session_id)

    at = at or datetime.now(timezone.utc)
    to_evict: list[str] = []
    for ref in sessions.list_refs(session_id):
        ok, _ = gate2_check(
            store, audit, session.principal, ref.doc_id, at,
            action="evict" if False else "context_hold", gate=3, session_id=session_id,
        )
        if not ok:
            to_evict.append(ref.doc_id)
            # A second audit row specifically for the eviction event. The
            # context_hold row records the check; the evict row records
            # the state change. Split so a compliance query for "when did
            # X get evicted from session Y" is a single-column filter.
            audit.append(
                AuditEntry(
                    principal=str(session.principal),
                    object_id=ref.doc_id,
                    action="evict",
                    decision="deny",
                    reason_path={"reason": "revalidation_failed"},
                    gate=3,
                    session_id=session_id,
                )
            )
    return sessions.evict_refs(session_id, to_evict)


def gate3_verify_citations(
    store: TupleStore,
    audit: AuditWriter,
    sessions: SessionStore,
    session_id: str,
    cited_doc_ids: list[str],
    at: datetime | None = None,
) -> list[str]:
    """Re-check every cited doc immediately before rendering the final
    answer. Return the list of stripped citations (docs the model tried
    to cite but the principal can no longer read).

    Callers should:
      - drop the stripped citations from the rendered answer
      - flag the sentences they came from (a citation stripped mid-answer
        may leave a claim un-attributable)
    """
    session = sessions.get(session_id)
    if session is None:
        raise SessionNotFound(session_id)

    at = at or datetime.now(timezone.utc)
    stripped: list[str] = []
    for doc_id in cited_doc_ids:
        ok, _ = gate2_check(
            store, audit, session.principal, doc_id, at,
            action="cite", gate=3, session_id=session_id,
        )
        if not ok:
            stripped.append(doc_id)
    return stripped
