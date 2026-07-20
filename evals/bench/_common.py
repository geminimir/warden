"""
Shared timing + table-formatting helpers for the 4 benchmark tables.

    p50/p90/p95/p99 microsecond timings, formatted the same way as
    Harvey's engineering blog. Non-negotiable honesty caveat: every
    table caption must include the corpus scale, whether it's synthetic,
    and that it's single-node. Do not report a number you don't have.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from typing import Callable


@dataclass
class Percentiles:
    p50_us: float
    p90_us: float
    p95_us: float
    p99_us: float
    n: int

    @classmethod
    def from_samples(cls, samples_us: list[float]) -> "Percentiles":
        s = sorted(samples_us)
        n = len(s)
        return cls(
            p50_us=statistics.median(s),
            p90_us=s[min(n - 1, int(n * 0.90))],
            p95_us=s[min(n - 1, int(n * 0.95))],
            p99_us=s[min(n - 1, int(n * 0.99))],
            n=n,
        )


def measure(op: Callable[[], object], n_iters: int, warmup: int = 5) -> Percentiles:
    """Run `op` n_iters times after `warmup` calls, return microsecond
    percentiles. `op` should be a no-arg lambda that performs one
    representative call."""
    for _ in range(warmup):
        op()
    samples: list[float] = []
    for _ in range(n_iters):
        t0 = time.perf_counter()
        op()
        t1 = time.perf_counter()
        samples.append((t1 - t0) * 1_000_000.0)
    return Percentiles.from_samples(samples)


HONESTY = (
    "> **Honesty:** these are synthetic, single-node numbers on a laptop-class "
    "machine. Do not extrapolate to production scale."
)


def table_header() -> list[str]:
    return ["| " + " | ".join(("scenario", "n", "p50 (µs)", "p90 (µs)", "p95 (µs)", "p99 (µs)")) + " |",
            "|" + "|".join(["---"] * 6) + "|"]


def table_row(label: str, p: Percentiles) -> str:
    return f"| {label} | {p.n} | {p.p50_us:.1f} | {p.p90_us:.1f} | {p.p95_us:.1f} | {p.p99_us:.1f} |"
