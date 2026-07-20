# Table 2 — Recall@20 vs. selectivity

For a synthetic 10k-doc corpus, vary a principal's authorization selectivity (fraction of the corpus they can read) and measure Recall@20 for three retrieval strategies.

> **Honesty:** these are synthetic, single-node numbers on a laptop-class machine. Do not extrapolate to production scale.

| selectivity | post-filter | id-list | label-pushdown |
|---|---|---|---|
| 0.100% | 0.07 | 1.00 | 1.00 |
| 0.500% | 0.07 | 1.00 | 1.00 |
| 1.000% | 0.07 | 1.00 | 1.00 |
| 5.000% | 0.14 | 1.00 | 1.00 |
| 10.000% | 0.28 | 1.00 | 1.00 |
| 50.000% | 0.81 | 1.00 | 1.00 |

**Read:** post-filter's recall collapses as selectivity drops (fewer authorized docs → top-K by pure similarity misses most). ID-list and label-pushdown maintain recall — but ID-list requires shipping |Authorized(u)| ids into the query, which is O(|corpus|) in the worst case. Label pushdown ships O(|L(u)|) ints, always.
