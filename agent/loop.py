"""
Minimal real agent loop wired to the gateway.

    "It does not need to be a good agent. It needs to be a REAL one — the
    claim 'evicted from in-flight agent context windows' requires an
    actual context window."
                                        — warden-engineering-doc.md, W3.5

What "real" means here:

  - Turn-based conversation history is threaded through every LLM call.
  - Between every turn, `gate3_revalidate` fires against the session.
  - When a doc gets evicted mid-run, a system message tells the model
    "[N sources removed]" so it can replan.
  - Before returning the final answer, `gate3_verify_citations` runs and
    stripped citations are reported alongside the text.

What "minimal" means:

  - One tool: `retrieve(query)`. Real production agents would have more;
    Warden's authorization semantics don't care.
  - No streaming, no parallel tool calls, no prompt engineering. The FakeLLM
    is scripted; a real LLM (Anthropic/OpenAI) plugs in behind the LLM
    Protocol.
  - `retrieve(query)` is where the ANN candidate step happens — for the W3
    scenarios we bypass ANN and let scenarios pass candidates directly, so
    tests don't need pgvector.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from agent.llm import LLM, Message, TextResponse, ToolCall
from core.store import TupleStore
from gateway.audit import AuditWriter
from gateway.gates import (
    gate3_revalidate,
    gate3_verify_citations,
    retrieve_authorized,
)
from gateway.session import Session, SessionStore

# A "candidate resolver" produces the pre-filtered candidate doc IDs for a
# given (principal, query). In production this calls retrieval/strategies.py
# and hits pgvector. In tests it's a lambda that returns a fixed list — same
# interface, different guts.
CandidateResolver = Callable[[str], list[str]]


@dataclass
class AgentResult:
    """What the agent produces at the end of a run."""

    answer: str
    citations: list[str]
    stripped_citations: list[str]
    session: Session
    # For diagnostics: the message history the LLM saw on the last turn.
    final_messages: list[Message] = field(default_factory=list)


def run_agent(
    *,
    llm: LLM,
    store: TupleStore,
    audit: AuditWriter,
    sessions: SessionStore,
    session_id: str,
    candidate_resolver: CandidateResolver,
    initial_query: str,
    system_prompt: str = "You are a helpful assistant. Use the retrieve() tool to answer.",
    max_turns: int = 5,
) -> AgentResult:
    """Run an agent turn loop against the gateway.

    Contract:

      - Every turn starts with `gate3_revalidate` firing. If any docs were
        evicted since the last turn, a system message reports them.
      - The LLM sees the full conversation history (system, user, tool
        results, evictions).
      - When the LLM emits a `retrieve` ToolCall, the loop:
          1. Resolves candidates via `candidate_resolver(query)` (Gate 1
             in production; a stub in tests).
          2. Runs `retrieve_authorized` (Gate 2 + session.add_refs).
          3. Appends a `tool` message with the allowed doc IDs.
      - When the LLM emits a TextResponse, the loop:
          1. Extracts citations (any doc_id present in the current session
             context AND mentioned in the text — see _extract_citations).
          2. Runs `gate3_verify_citations`.
          3. Returns AgentResult.
      - If the model never returns a TextResponse within `max_turns`,
        raise — a runaway loop shouldn't silently pass.
    """
    session = sessions.get(session_id)
    if session is None:
        raise ValueError(f"unknown session_id: {session_id!r}")

    messages: list[Message] = [
        Message(role="system", content=system_prompt),
        Message(role="user", content=initial_query),
    ]

    for turn_i in range(max_turns):
        # Gate 3 revalidation: BEFORE the LLM call, not after. That way if a
        # revocation just landed, this turn's model prompt already reflects it.
        evicted = gate3_revalidate(store, audit, sessions, session_id)
        if evicted:
            messages.append(
                Message(
                    role="system",
                    content=(
                        f"[{len(evicted)} source(s) removed: access revoked] "
                        f"doc_ids={sorted(r.doc_id for r in evicted)}"
                    ),
                )
            )

        out = llm.turn(messages)

        if isinstance(out, ToolCall):
            if out.name != "retrieve":
                raise ValueError(
                    f"agent loop: only 'retrieve' is a supported tool; got {out.name!r}"
                )
            query = out.arguments.get("query", "")
            candidates = candidate_resolver(query)
            kept = retrieve_authorized(
                store, audit, sessions, session_id, candidates,
            )
            messages.append(Message(role="assistant", content=f"<tool_use retrieve query={query!r}/>"))
            messages.append(
                Message(
                    role="tool",
                    content=f"retrieved doc_ids: {sorted(r.doc_id for r in kept)}",
                    tool_result={"doc_ids": [r.doc_id for r in kept]},
                )
            )
            continue

        if isinstance(out, TextResponse):
            citations = _extract_citations(out.text, sessions.list_refs(session_id))
            stripped = gate3_verify_citations(
                store, audit, sessions, session_id, citations,
            )
            return AgentResult(
                answer=out.text,
                citations=citations,
                stripped_citations=stripped,
                session=sessions.get(session_id),  # type: ignore[arg-type]
                final_messages=messages,
            )

        raise TypeError(f"agent loop: unexpected LLM output type {type(out).__name__}")

    raise RuntimeError(
        f"agent loop: reached max_turns={max_turns} without a final answer"
    )


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

def _extract_citations(text: str, current_refs) -> list[str]:
    """Return doc_ids from the session context that appear as tokens in `text`.

    Kept intentionally naive: real citation extraction is a whole subfield.
    For W3 scenarios, we assume the model writes doc_ids verbatim (that's
    what the FakeLLM scripts do). Production would use a citation schema
    that the model is prompted to emit, e.g. `<cite id="d1"/>`.
    """
    ids_in_context = {r.doc_id for r in current_refs}
    tokens = set(text.replace(",", " ").replace(".", " ").split())
    return sorted(ids_in_context & tokens)
