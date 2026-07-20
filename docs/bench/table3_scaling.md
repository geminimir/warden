# Table 3 — Predicate size vs. corpus size

For a principal with mid-tier authorization (selectivity ~5%), measure the size of the filter predicate shipped into Postgres by each strategy.

> **Honesty:** these are synthetic, single-node numbers on a laptop-class machine. Do not extrapolate to production scale.

| corpus size | ID-list expansion (ints) | label pushdown (ints) |
|---|---|---|
| 1k (1,000) | max 206 | max 6 |
| 10k (10,000) | max 1,971 | max 6 |
| 50k (50,000) | max 9,663 | max 6 |

**Read:** the ID-list column grows roughly linearly with corpus size at fixed selectivity — a firm-wide partner authorized on 10M documents would need a 10M-element `IN` clause. The label-pushdown column is bounded by the number of groups/orgs the principal is a member of, which is corpus-independent.
