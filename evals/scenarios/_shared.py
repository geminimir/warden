"""
Shared helpers for the 10 adversarial scenarios.

Every scenario returns a `ScenarioResult(passed, details)` and each has a
matching naive-baseline counterpart. The suite runner (`run_all.py`) tallies
`leaks = 0/10` for Warden and `leaks = N/10` for the naive baseline.

Two naive baselines, chosen per scenario:

    naive_rag_retrieve(query_id, k)
        Pure top-K by (fake) similarity. No authorization filter at all.
        Used for scenarios where the leak is a retrieval issue (semantic
        neighbor, deleted-but-embedded, etc.).

    naive_authz_check(store, principal, obj)
        Returns True if any grant path exists. Ignores barriers, ignores
        expiry. Used for scenarios where the leak is an authorization
        semantic that a naive engine gets wrong (ethical wall, expired
        guest, etc.).

Both baselines are DELIBERATELY WRONG. They exist to be beaten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core.algebra import GRANT_RELATIONS, Object, Subject
from core.store import TupleStore

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
YESTERDAY = NOW - timedelta(days=1)
TOMORROW = NOW + timedelta(days=1)


@dataclass
class ScenarioResult:
    passed: bool
    details: str


# ---------------------------------------------------------------------------
# Naive baselines
# ---------------------------------------------------------------------------

def naive_authz_check(store: TupleStore, principal: Subject, obj: Object) -> bool:
    """Return True iff ANY grant path exists from principal to obj.

    Deliberately broken:
      - No barrier check (deny is invisible)
      - No expiry check (revoked-via-expiry is still allowed)
      - Same DFS as the real engine so the naive "leak" is only in the
        SEMANTIC, not in the traversal.
    """
    depth_limit = 8  # match algebra.MAX_DEPTH so we're comparing apples to apples

    def dfs(subject: Subject, remaining: int, seen: set[tuple[str, str]]) -> bool:
        if remaining <= 0:
            return False
        key = (subject.type, subject.id)
        if key in seen:
            return False
        seen.add(key)
        for t in store.outgoing(subject):
            if t.relation not in GRANT_RELATIONS:
                continue
            # NO expiry check here — that's the point.
            if t.object.type == obj.type and t.object.id == obj.id:
                return True
            if t.object.type in ("group", "org", "matter", "space"):
                next_subject = Subject(
                    type=t.object.type if t.object.type in ("group", "org") else "group",
                    id=t.object.id,
                )
                if dfs(next_subject, remaining - 1, seen):
                    return True
        return False

    return dfs(principal, depth_limit, set())


def naive_rag_retrieve(
    candidate_ids: list[str],
    scores: dict[str, float],
    k: int,
) -> list[str]:
    """Naive top-K retrieval — no authorization filter at all.

    Given a set of candidates and their similarity scores, return the top
    k by score. This is what a plain vanilla vector search does; it has
    no idea who is asking.
    """
    ranked = sorted(candidate_ids, key=lambda c: -scores.get(c, 0.0))
    return ranked[:k]
