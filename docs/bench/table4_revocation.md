# Table 4 — Revocation propagation

Time from `revoke()` to the doc being (a) DENIED by the authoritative check, and (b) EVICTED from an in-flight agent session. Both are next-call — Warden does not buffer authorization decisions across calls.

> **Honesty:** these are synthetic, single-node numbers on a laptop-class machine. Do not extrapolate to production scale.

| scenario | n | p50 (µs) | p90 (µs) | p95 (µs) | p99 (µs) |
|---|---|---|---|---|---|
| revoke → deny (rebac.check) | 100 | 3.0 | 3.0 | 3.1 | 3.3 |
| revoke → evict (gate3_revalidate) | 100 | 17.1 | 17.5 | 17.8 | 23.8 |

**Read:** revocation is effectively synchronous. There is no propagation delay; the next call sees the new state.
