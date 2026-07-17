"""
Scenario 4: in-flight revocation.

    The one no other system on the market passes.

Setup:
    - Alice has viewer on doc:acme_merger.
    - Alice starts a session, retrieves the doc, adds it to context.
    - Bob (an admin, via /tuples) revokes Alice's grant while the
      agent is mid-run.
    - The agent takes another turn. Gate 3 fires, evicts the doc,
      injects `[1 source removed]`, and the model replans.

Assertion: after the revocation turn, the doc is NOT in Alice's session
context, an evict row appears in the audit log, and the model's next
message reflects the eviction (via the system-injected notice).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.llm import FakeLLM, TextResponse, ToolCall, script
from agent.loop import run_agent
from core.algebra import Graph, Object, Subject, Tuple
from core.store import InMemoryStore
from gateway.api import _rebuild_in_memory_store
from gateway.audit import InMemoryAuditLog
from gateway.session import InMemorySessionStore


@dataclass
class ScenarioResult:
    passed: bool
    details: str


def run() -> ScenarioResult:
    # ---- setup ----------------------------------------------------------
    grant = Tuple(
        subject=Subject("user", "alice"),
        relation="viewer",
        object=Object("doc", "acme_merger"),
    )
    store = InMemoryStore(
        Graph(tuples=frozenset({grant}), barriers=frozenset(), documents=frozenset())
    )
    audit = InMemoryAuditLog()
    sessions = InMemorySessionStore()
    session = sessions.create(Subject("user", "alice"))

    # A candidate resolver that returns the merger doc regardless of query.
    # Gate 1 in production; a stub here because scenario 4 is about Gate 3,
    # not the pre-filter.
    def resolver(_query: str) -> list[str]:
        return ["acme_merger"]

    # Scripted turns:
    #   1. retrieve("What are the merger terms?")
    #   2. TextResponse mentioning the doc, cite it — this final turn
    #      happens AFTER the revocation. Gate 3 evicts before the LLM
    #      sees this turn's prompt, so the model gets the eviction notice
    #      and the doc is no longer in context. We assert the model's
    #      output reflects that reality.
    fake = FakeLLM(script=script(
        ToolCall(name="retrieve", arguments={"query": "merger terms"}),
        TextResponse(text=(
            "I was going to cite acme_merger but it appears the source "
            "was removed."
        )),
    ))

    # ---- Turn 1: retrieve ----------------------------------------------
    # (run_agent handles this internally; but we need to break BETWEEN
    # turns 1 and 2 to revoke access. Do the retrieve half manually.)
    from gateway.gates import retrieve_authorized
    retrieve_authorized(store, audit, sessions, session.session_id, ["acme_merger"])
    assert [r.doc_id for r in sessions.list_refs(session.session_id)] == ["acme_merger"], (
        "setup failure: alice should hold acme_merger in context after retrieve"
    )

    # ---- Revoke access mid-run -----------------------------------------
    _rebuild_in_memory_store(store, remove=grant)

    # ---- Turn 2: run one more turn; Gate 3 must evict -----------------
    # Reset the fake LLM to just the final TextResponse for this second
    # invocation of the loop.
    fake2 = FakeLLM(script=script(
        TextResponse(text=(
            "The document I was going to cite is no longer accessible."
        )),
    ))
    result = run_agent(
        llm=fake2,
        store=store,
        audit=audit,
        sessions=sessions,
        session_id=session.session_id,
        candidate_resolver=resolver,
        initial_query="Continue.",
        max_turns=2,
    )

    # ---- Assertions ----------------------------------------------------
    refs_after = [r.doc_id for r in sessions.list_refs(session.session_id)]
    if refs_after:
        return ScenarioResult(False, f"expected empty context, got {refs_after}")

    audit_rows = audit.all()
    evictions = [e for e in audit_rows if e.action == "evict"]
    if len(evictions) != 1 or evictions[0].object_id != "acme_merger":
        return ScenarioResult(
            False, f"expected one evict row for acme_merger; got {evictions}"
        )

    # The system-injected eviction notice must appear in the message
    # history that the LLM saw on its final turn.
    final_msgs = "\n".join(m.content for m in result.final_messages)
    if "acme_merger" not in final_msgs or "removed" not in final_msgs:
        return ScenarioResult(
            False,
            "eviction notice not visible in LLM message history — "
            "the model would have had no way to replan",
        )

    return ScenarioResult(
        True,
        "in-flight revocation: doc evicted from context, audit trail recorded, "
        "model saw the eviction notice",
    )


if __name__ == "__main__":
    r = run()
    print(f"[{'PASS' if r.passed else 'FAIL'}] scenario_04_inflight_revocation: {r.details}")
    raise SystemExit(0 if r.passed else 1)
