"""
Runs all four benchmark tables and writes them to docs/bench/*.md.

    make bench

Each table script is standalone (run individually via
`python -m evals.bench.tableN_...`), and this orchestrator just
sequences them.
"""

from __future__ import annotations

from pathlib import Path

from evals.bench import (
    table1_overhead,
    table2_recall,
    table3_scaling,
    table4_revocation,
)

DOCS_DIR = Path("docs/bench")


def main() -> int:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    print("Running Table 1 — Authorization overhead...")
    table1_overhead.run(DOCS_DIR / "table1_overhead.md")
    print("Running Table 2 — Recall vs. selectivity...")
    table2_recall.run(DOCS_DIR / "table2_recall.md")
    print("Running Table 3 — Predicate size vs. corpus size...")
    table3_scaling.run(DOCS_DIR / "table3_scaling.md")
    print("Running Table 4 — Revocation propagation...")
    table4_revocation.run(DOCS_DIR / "table4_revocation.md")
    print("\nAll 4 tables written to docs/bench/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
