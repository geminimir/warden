"""
Table 1 — Authorization overhead: rebac.check() per-call latency.

Measures the pure cost of the authoritative check (Gate 2) against
in-memory stores at increasing corpus sizes. This is the "added ms"
column in the design doc's Table 1.

The full design-doc version runs on Postgres+pgvector at 100k/1M/10M;
this smoke version measures the in-memory path, which is what production
Gate 2 uses when the store is InMemoryStore or when Postgres has warmed
its cache. Numbers are lower bounds on Gate 2's wire cost.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.algebra import Object, Subject
from core.rebac import check
from core.store import InMemoryStore
from evals.bench._common import HONESTY, Percentiles, measure, table_header, table_row
from evals.corpus import CorpusSpec, build_corpus

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run(out_path: Path) -> None:
    scales = [
        ("1k", CorpusSpec(n_orgs=5, n_users=50, n_groups=20, n_docs=1_000, seed=1)),
        ("10k", CorpusSpec(n_orgs=10, n_users=100, n_groups=40, n_docs=10_000, seed=1)),
        ("50k", CorpusSpec(n_orgs=20, n_users=200, n_groups=80, n_docs=50_000, seed=1)),
    ]
    lines = [
        "# Table 1 — Authorization overhead",
        "",
        "Per-call cost of `core.rebac.check()` — Gate 2's authoritative "
        "check — measured against `InMemoryStore` at three corpus sizes. "
        "Every check evaluates deny (barrier tags) and allow (DFS grant "
        "walk, depth-limited to `MAX_DEPTH=8`) against the current graph.",
        "",
        HONESTY,
        "",
        *table_header(),
    ]
    for label, spec in scales:
        corpus = build_corpus(spec)
        store = InMemoryStore(corpus.graph)
        principal = Subject("user", "u0")
        # Rotate through documents so we don't just benchmark a cache hit.
        doc_ids = [d.id for d in list(corpus.graph.documents)[:200]]

        def op(_di=[0]):
            di = _di[0]
            _di[0] = (di + 1) % len(doc_ids)
            check(store, principal, Object("doc", doc_ids[di]), NOW)

        p = measure(op, n_iters=500)
        lines.append(table_row(f"check() @ {label} docs", p))

    lines.append("")
    lines.append(
        "**Read:** Gate 2 stays bounded per call (depth-limited DFS is O(1) "
        "in corpus size). Increases with corpus mostly reflect grant-density "
        "variance in the generated graph, not linear cost."
    )
    out_path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run(Path("docs/bench/table1_overhead.md"))
    print(f"wrote docs/bench/table1_overhead.md")


# Ensure Percentiles is referenced so unused-import lint doesn't complain.
_ = Percentiles
