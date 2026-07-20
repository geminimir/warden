"""
Parameterized synthetic corpus generator.

    make corpus SCALE=10k         # 10,000 docs
    make corpus SCALE=1M          # 1,000,000 docs

Deterministic under a seed. Same seed → identical Graph + embeddings, so
benchmark numbers reproduce exactly across runs.

    Honesty constraint: these are SYNTHETIC, SINGLE-NODE numbers. The
    README and every benchmark table must say so plainly. Do not
    extrapolate to production scale.

Composition (defaults):

    500 orgs, 10k principals, 2k matters, 300 spaces, 200 barriers.
    n docs distributed across orgs with a realistic power-law skew
    (top 20% of orgs hold 80% of docs). Selectivity per principal
    is controllable via the `selectivity` knob — the fraction of
    the corpus each principal is authorized on.

Embeddings are 768-d gaussian with per-doc jitter. They are NOT drawn
from a real IR corpus; the recall numbers we care about are RELATIVE
(strategy A vs. strategy B), not absolute vs. some real-world baseline.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterator

import numpy as np

from core.algebra import (
    Barrier,
    Document,
    Graph,
    Object,
    Subject,
    Tuple,
    tag,
)


@dataclass(frozen=True)
class CorpusSpec:
    n_orgs: int = 20
    n_users: int = 200
    n_groups: int = 40
    n_matters: int = 50
    n_barriers: int = 8
    n_docs: int = 10_000
    # Selectivity: average fraction of the corpus each principal is
    # authorized on. Controls how many grant tuples we emit. Realistic
    # values are 0.001 to 0.05 for enterprise; 0.5+ for "everyone reads
    # everything" toy corpora.
    selectivity: float = 0.02
    # Barrier density: fraction of docs behind at least one barrier.
    barrier_density: float = 0.10
    embedding_dim: int = 768
    seed: int = 42


@dataclass
class Corpus:
    """A corpus is the Graph (for the ReBAC engine) plus embeddings
    (for pgvector). Kept as one bundle so callers don't have to thread
    two data structures through the benchmark loops."""

    graph: Graph
    embeddings: dict[str, list[float]]
    spec: CorpusSpec

    def sample_query_embedding(self, rng: random.Random | None = None) -> list[float]:
        """Return a synthetic query vector. Not tied to any doc — approximates
        an arbitrary user query in the same embedding space."""
        r = rng or random.Random(self.spec.seed + 999)
        return [r.gauss(0.0, 1.0) for _ in range(self.spec.embedding_dim)]

    def principals(self) -> Iterator[Subject]:
        for i in range(self.spec.n_users):
            yield Subject("user", f"u{i}")

    def documents(self) -> Iterator[Document]:
        yield from self.graph.documents


def build_corpus(spec: CorpusSpec | None = None) -> Corpus:
    """Construct a Corpus deterministically from a CorpusSpec."""
    spec = spec or CorpusSpec()
    rng = random.Random(spec.seed)
    np_rng = np.random.default_rng(spec.seed)

    # ---- authorization graph -------------------------------------------
    tuples: set[Tuple] = set()
    # Every user belongs to 1-3 groups and 1 org.
    for i in range(spec.n_users):
        user = Subject("user", f"u{i}")
        for _ in range(rng.randint(1, 3)):
            g = rng.randrange(spec.n_groups)
            tuples.add(Tuple(user, "member", Object("group", f"g{g}")))
        o = rng.randrange(spec.n_orgs)
        tuples.add(Tuple(user, "member", Object("org", f"o{o}")))

    # Some group→group nesting to exercise transitive walks.
    for _ in range(spec.n_groups // 3):
        a, b = rng.sample(range(spec.n_groups), 2)
        tuples.add(
            Tuple(
                Subject("group", f"g{a}"),
                "member",
                Object("group", f"g{b}"),
            )
        )

    # ---- barriers ------------------------------------------------------
    barriers = frozenset(
        Barrier(
            id=i + 1,
            name=f"wall_{i}",
            side_a=f"g{rng.randrange(spec.n_groups)}",
            side_b=f"g{(rng.randrange(spec.n_groups) + 1) % spec.n_groups}",
        )
        for i in range(spec.n_barriers)
    )
    # Deduplicate any accidental self-side barriers.
    barriers = frozenset(b for b in barriers if b.side_a != b.side_b)

    # ---- docs + grants + embeddings ------------------------------------
    documents_out: list[Document] = []
    embeddings: dict[str, list[float]] = {}
    # Power-law org distribution: top 20% of orgs hold ~80% of docs.
    org_weights = [1.0 / (i + 1) ** 1.5 for i in range(spec.n_orgs)]
    org_pool = _weighted_sample(org_weights, spec.n_docs, rng)

    # Expected grants per doc, chosen so each user sees roughly `selectivity`
    # fraction of the corpus. E[grants per doc] ≈ selectivity * n_users
    # divided by n_users; simpler: pick k such that each doc is grant-able
    # from ceil(selectivity * n_users) groups on average.
    grants_per_doc = max(1, int(math.ceil(spec.selectivity * spec.n_groups)))

    for i in range(spec.n_docs):
        did = f"d{i}"
        org_id = f"o{org_pool[i]}"

        # Grants: pick a small handful of groups to be viewers/owners.
        grant_groups = rng.sample(range(spec.n_groups), min(grants_per_doc, spec.n_groups))
        for g in grant_groups:
            tuples.add(
                Tuple(
                    Subject("group", f"g{g}"),
                    "viewer" if rng.random() < 0.8 else "owner",
                    Object("doc", did),
                )
            )

        # Barrier tag with `barrier_density` probability.
        barrier_tags: frozenset[int] = frozenset()
        if barriers and rng.random() < spec.barrier_density:
            b = rng.choice(list(barriers))
            barrier_tags = frozenset({tag(b.id, rng.choice([0, 1]))})

        documents_out.append(
            Document(id=did, org_id=org_id, barrier_tags=barrier_tags)
        )

        # Embedding: gaussian noise; small per-org bias so partition
        # filtering has something to prune on.
        base = np_rng.normal(loc=org_pool[i] * 0.01, scale=1.0, size=spec.embedding_dim)
        embeddings[did] = base.tolist()

    graph = Graph(
        tuples=frozenset(tuples),
        barriers=barriers,
        documents=frozenset(documents_out),
    )
    return Corpus(graph=graph, embeddings=embeddings, spec=spec)


def _weighted_sample(weights: list[float], n: int, rng: random.Random) -> list[int]:
    """Sample n indexes proportional to `weights`. random.choices with
    a weights arg, kept explicit to survive Python version churn."""
    return rng.choices(range(len(weights)), weights=weights, k=n)


# ---------------------------------------------------------------------------
# Scale presets
# ---------------------------------------------------------------------------

SCALES = {
    "smoke": CorpusSpec(n_orgs=5, n_users=20, n_groups=10, n_docs=200, seed=1),
    "small": CorpusSpec(n_orgs=10, n_users=50, n_groups=20, n_docs=2_000, seed=1),
    "10k": CorpusSpec(n_docs=10_000),
    "100k": CorpusSpec(n_docs=100_000, n_users=1_000, n_groups=100),
    "1m": CorpusSpec(n_docs=1_000_000, n_users=5_000, n_groups=500),
}


def scale(name: str) -> CorpusSpec:
    if name not in SCALES:
        raise ValueError(f"unknown scale {name!r}; known: {sorted(SCALES)}")
    return SCALES[name]
