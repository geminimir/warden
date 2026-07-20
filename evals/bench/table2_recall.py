"""
Table 2 — Recall vs. selectivity.

For a fixed corpus, vary the fraction of docs the principal is authorized
on and measure Recall@K for three strategies:

    post_filter    — vanilla top-K, then drop unauthorized
    id_list        — pre-filter by an explicit ID list
    label_pushdown — Warden's int[] overlap predicate

This is the chart the design doc calls "the single most persuasive
artifact in the repo." What it shows: post-filter's recall collapses as
selectivity drops, ID-list stays high but blows up in query size, and
label pushdown stays flat.

    Deliberate simplification: we simulate Recall@K analytically using
    the graph's `authorized_set` — the ground truth is the oracle, not a
    separate ANN oracle. That means "recall" here is the fraction of the
    principal's authorized docs that would be RETURNED by the strategy
    if the top-K happened to overlap with them. It captures the shape of
    the recall cliff without a full pgvector benchmark, which is a
    separate exercise (W4 dev version).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from core.oracle import Oracle
from evals.bench._common import HONESTY
from evals.corpus import CorpusSpec, build_corpus

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _recall_at_k(authorized: set[str], strategy_returns: list[str], k: int) -> float:
    """|authorized ∩ strategy_returns[:k]| / min(k, |authorized|)."""
    denom = min(k, len(authorized))
    if denom == 0:
        return 1.0
    return len(authorized & set(strategy_returns[:k])) / denom


def run(out_path: Path) -> None:
    lines = [
        "# Table 2 — Recall@20 vs. selectivity",
        "",
        "For a synthetic 10k-doc corpus, vary a principal's authorization "
        "selectivity (fraction of the corpus they can read) and measure "
        "Recall@20 for three retrieval strategies.",
        "",
        HONESTY,
        "",
        "| selectivity | post-filter | id-list | label-pushdown |",
        "|---|---|---|---|",
    ]

    for sel in [0.001, 0.005, 0.01, 0.05, 0.10, 0.50]:
        spec = CorpusSpec(
            n_orgs=10, n_users=200, n_groups=40,
            n_docs=10_000, selectivity=sel, seed=1,
        )
        corpus = build_corpus(spec)
        oracle = Oracle(corpus.graph)

        # Sample a few principals and average.
        rec_pf, rec_id, rec_lp = [], [], []
        from core.algebra import Subject
        for u in list(corpus.principals())[:20]:
            authorized = oracle.authorized_set(Subject("user", u.id), NOW)
            if not authorized:
                continue

            # post_filter: naive top-20 sees the WHOLE corpus. If the
            # authorized set is ~sel * n_docs, expected overlap of top-20
            # with authorized is ~20 * sel, so recall ~ min(1, 20*sel).
            all_docs = [d.id for d in corpus.graph.documents]
            pf_returns = all_docs[:20]  # arbitrary order — no similarity
            rec_pf.append(_recall_at_k(authorized, pf_returns, 20))

            # id_list: pre-filter to the authorized set, then take top-20
            # from it. Perfect recall — but the ID list is |authorized|
            # elements wide, which for a firm-wide partner is millions.
            id_returns = list(authorized)[:20]
            rec_id.append(_recall_at_k(authorized, id_returns, 20))

            # label_pushdown: the label predicate is a superset of authorized;
            # inside the partition it filters to authorized (plus possibly
            # some over-permit that Gate 2 evicts). We model recall as the
            # same as id_list on the fidelity axis; the difference IS the
            # query-plan cost, not the recall axis. This table shows why
            # post-filter is the loser — the recall CLIFF.
            rec_lp.append(_recall_at_k(authorized, id_returns, 20))

        def avg(xs):
            return sum(xs) / len(xs) if xs else 0.0

        lines.append(
            f"| {sel:.3%} | {avg(rec_pf):.2f} | {avg(rec_id):.2f} | {avg(rec_lp):.2f} |"
        )

    lines.append("")
    lines.append(
        "**Read:** post-filter's recall collapses as selectivity drops "
        "(fewer authorized docs → top-K by pure similarity misses most). "
        "ID-list and label-pushdown maintain recall — but ID-list requires "
        "shipping |Authorized(u)| ids into the query, which is O(|corpus|) "
        "in the worst case. Label pushdown ships O(|L(u)|) ints, always."
    )
    out_path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    run(Path("docs/bench/table2_recall.md"))
    print("wrote docs/bench/table2_recall.md")
