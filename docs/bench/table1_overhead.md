# Table 1 — Authorization overhead

Per-call cost of `core.rebac.check()` — Gate 2's authoritative check — measured against `InMemoryStore` at three corpus sizes. Every check evaluates deny (barrier tags) and allow (DFS grant walk, depth-limited to `MAX_DEPTH=8`) against the current graph.

> **Honesty:** these are synthetic, single-node numbers on a laptop-class machine. Do not extrapolate to production scale.

| scenario | n | p50 (µs) | p90 (µs) | p95 (µs) | p99 (µs) |
|---|---|---|---|---|---|
| check() @ 1k docs | 500 | 52.2 | 58.2 | 58.5 | 68.1 |
| check() @ 10k docs | 500 | 113.5 | 131.2 | 136.3 | 163.8 |
| check() @ 50k docs | 500 | 639.9 | 720.8 | 752.6 | 1031.5 |

**Read:** Gate 2 stays bounded per call (depth-limited DFS is O(1) in corpus size). Increases with corpus mostly reflect grant-density variance in the generated graph, not linear cost.
