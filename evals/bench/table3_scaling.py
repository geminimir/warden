"""
Table 3 — Scaling: label pushdown is O(1) in corpus size; ID-list expansion is O(n).

The claim this table makes true: "capability-label pushdown, O(1) in
corpus size where ID-list expansion is O(n)."

We measure the SIZE OF THE PREDICATE that would be pushed into the
retrieval query. Query wire-time scales roughly with predicate size;
runtime scales with it too (Postgres has to hash-probe it). The table
shows that label pushdown's predicate is bounded by |L(u)| (tens to
hundreds), while ID-list expansion's predicate grows linearly with the
principal's authorized-set size.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.algebra import Subject
from core.labels import labels_for
from core.oracle import Oracle
from core.store import InMemoryStore
from evals.bench._common import HONESTY
from evals.corpus import CorpusSpec, build_corpus

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def run(out_path: Path) -> None:
    scales = [
        ("1k",  1_000),
        ("10k", 10_000),
        ("50k", 50_000),
    ]
    lines = [
        "# Table 3 — Predicate size vs. corpus size",
        "",
        "For a principal with mid-tier authorization "
        "(selectivity ~5%), measure the size of the filter predicate "
        "shipped into Postgres by each strategy.",
        "",
        HONESTY,
        "",
        "| corpus size | ID-list expansion (ints) | label pushdown (ints) |",
        "|---|---|---|",
    ]

    for label, n_docs in scales:
        spec = CorpusSpec(
            n_orgs=20, n_users=100, n_groups=40,
            n_docs=n_docs, selectivity=0.05, seed=1,
        )
        corpus = build_corpus(spec)
        oracle = Oracle(corpus.graph)
        store = InMemoryStore(corpus.graph)

        # Sample a few principals; report the max (worst case) predicate size.
        id_list_sizes = []
        label_pushdown_sizes = []
        for u in list(corpus.principals())[:20]:
            principal = Subject("user", u.id)
            id_list_sizes.append(len(oracle.authorized_set(principal, NOW)))
            L, _B = labels_for(store, principal)
            label_pushdown_sizes.append(len(L))
        lines.append(
            f"| {label} ({n_docs:,}) | max {max(id_list_sizes):,} | max {max(label_pushdown_sizes):,} |"
        )

    lines.append("")
    lines.append(
        "**Read:** the ID-list column grows roughly linearly with corpus "
        "size at fixed selectivity — a firm-wide partner authorized on 10M "
        "documents would need a 10M-element `IN` clause. The label-pushdown "
        "column is bounded by the number of groups/orgs the principal is a "
        "member of, which is corpus-independent."
    )
    out_path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run(Path("docs/bench/table3_scaling.md"))
    print("wrote docs/bench/table3_scaling.md")
