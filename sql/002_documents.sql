-- W2 schema: documents with flattened capability labels, barrier tags,
-- and a pgvector embedding column. LIST-partitioned by org_id.

CREATE EXTENSION IF NOT EXISTS vector;

-- Parent partitioned table. Never populated directly; every insert lands in
-- a per-org partition. Partitioning is defense-in-depth: even a total
-- failure of the label materialization logic cannot cross an org boundary,
-- because a query scoped to one org's partition never touches another
-- partition's heap.
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT NOT NULL,
    org_id       TEXT NOT NULL,
    acl_labels   INT[] NOT NULL,        -- flattened grant sources
    barrier_tags INT[] NOT NULL DEFAULT '{}',
    labels_epoch BIGINT NOT NULL DEFAULT 0,
    content      TEXT,
    embedding    vector(768),
    PRIMARY KEY (id, org_id)            -- includes partition key per PG rules
) PARTITION BY LIST (org_id);

-- GIN + HNSW live on the partitions, not the parent (a Postgres constraint
-- for partitioned tables). The retrieval code creates per-partition indexes
-- when it adds a new org partition; see core/postgres_store.py:ensure_org_partition.

-- A default partition catches orgs we haven't materialized a partition for
-- yet. In production you'd forbid this and require explicit partition
-- creation; for W2 dev / property tests, the default keeps things simple.
CREATE TABLE IF NOT EXISTS documents_default
    PARTITION OF documents DEFAULT;

CREATE INDEX IF NOT EXISTS documents_default_acl_gin
    ON documents_default USING GIN (acl_labels);
CREATE INDEX IF NOT EXISTS documents_default_barrier_gin
    ON documents_default USING GIN (barrier_tags);
-- HNSW on the default partition. Per-org partitions get their own HNSW
-- index in ensure_org_partition().
CREATE INDEX IF NOT EXISTS documents_default_embedding_hnsw
    ON documents_default USING hnsw (embedding vector_cosine_ops);
