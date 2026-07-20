"""
Table 4 — Revocation propagation.

Time from `revoke()` (delete the tuple) to:
    (a) doc is DENIED by rebac.check() — the authoritative gate.
    (b) doc is EVICTED from an in-flight agent session — Gate 3.

Both are effectively "next call" — Warden doesn't buffer authorization.
This table demonstrates the temporal guarantee (G4 in the design doc).
"""

from __future__ import annotations

import time
from pathlib import Path

from core.algebra import Graph, Object, Subject, Tuple
from core.store import InMemoryStore
from evals.bench._common import HONESTY
from gateway.api import _rebuild_in_memory_store
from gateway.audit import InMemoryAuditLog
from gateway.gates import gate3_revalidate, retrieve_authorized
from gateway.session import InMemorySessionStore

from datetime import datetime, timezone

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _measure_revoke_to_deny() -> float:
    grant = Tuple(Subject("user", "alice"), "viewer", Object("doc", "d1"))
    store = InMemoryStore(
        Graph(tuples=frozenset({grant}), barriers=frozenset(), documents=frozenset())
    )
    from core.rebac import check
    # Sanity: currently allowed.
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    assert ok
    t0 = time.perf_counter()
    _rebuild_in_memory_store(store, remove=grant)
    ok, _ = check(store, Subject("user", "alice"), Object("doc", "d1"), NOW)
    t1 = time.perf_counter()
    assert not ok, "revoke did not deny"
    return (t1 - t0) * 1_000_000.0


def _measure_revoke_to_evict() -> float:
    grant = Tuple(Subject("user", "alice"), "viewer", Object("doc", "d1"))
    store = InMemoryStore(
        Graph(tuples=frozenset({grant}), barriers=frozenset(), documents=frozenset())
    )
    audit = InMemoryAuditLog()
    sessions = InMemorySessionStore()
    session = sessions.create(Subject("user", "alice"))
    retrieve_authorized(store, audit, sessions, session.session_id, ["d1"])
    assert [r.doc_id for r in sessions.list_refs(session.session_id)] == ["d1"]

    t0 = time.perf_counter()
    _rebuild_in_memory_store(store, remove=grant)
    evicted = gate3_revalidate(store, audit, sessions, session.session_id)
    t1 = time.perf_counter()
    assert [r.doc_id for r in evicted] == ["d1"]
    return (t1 - t0) * 1_000_000.0


def run(out_path: Path) -> None:
    # Warm up + collect.
    for _ in range(20):
        _measure_revoke_to_deny()
        _measure_revoke_to_evict()

    deny_samples = [_measure_revoke_to_deny() for _ in range(100)]
    evict_samples = [_measure_revoke_to_evict() for _ in range(100)]

    from evals.bench._common import Percentiles, table_header, table_row
    p_deny = Percentiles.from_samples(deny_samples)
    p_evict = Percentiles.from_samples(evict_samples)

    lines = [
        "# Table 4 — Revocation propagation",
        "",
        "Time from `revoke()` to the doc being (a) DENIED by the "
        "authoritative check, and (b) EVICTED from an in-flight agent "
        "session. Both are next-call — Warden does not buffer authorization "
        "decisions across calls.",
        "",
        HONESTY,
        "",
        *table_header(),
        table_row("revoke → deny (rebac.check)", p_deny),
        table_row("revoke → evict (gate3_revalidate)", p_evict),
        "",
        "**Read:** revocation is effectively synchronous. There is no "
        "propagation delay; the next call sees the new state.",
    ]
    out_path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run(Path("docs/bench/table4_revocation.md"))
    print("wrote docs/bench/table4_revocation.md")
