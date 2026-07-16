-- Warden schema, initial migration (W1).
--
-- Two tables: `tuples` (Zanzibar-shaped relationship state) and `barriers`
-- (information walls / deny layer). `documents` and pgvector arrive in W2.

CREATE TABLE IF NOT EXISTS tuples (
    subject_type TEXT NOT NULL,               -- user | group | org
    subject_id   TEXT NOT NULL,
    subject_rel  TEXT NOT NULL DEFAULT '',    -- userset rewrite: group:eng#member
    relation     TEXT NOT NULL,               -- member | parent | viewer | owner
    object_type  TEXT NOT NULL,               -- org | matter | space | group | doc
    object_id    TEXT NOT NULL,
    expires_at   TIMESTAMPTZ,                 -- NULL = permanent
    PRIMARY KEY (subject_type, subject_id, subject_rel, relation, object_type, object_id)
);

-- Forward index: "who has relation R on object X?"
-- Used by expand().
CREATE INDEX IF NOT EXISTS tuples_fwd
    ON tuples (object_type, object_id, relation);

-- Reverse index: "what does subject S touch?"
-- Used by the check() outgoing() walk.
CREATE INDEX IF NOT EXISTS tuples_rev
    ON tuples (subject_type, subject_id);

CREATE TABLE IF NOT EXISTS barriers (
    id     BIGSERIAL PRIMARY KEY,
    name   TEXT NOT NULL,
    side_a TEXT NOT NULL,                     -- group id
    side_b TEXT NOT NULL,
    CHECK (side_a <> side_b)                  -- a barrier between a side and itself is meaningless
);

-- Barriers are small in count (single digits to low tens even for large
-- tenants), so no secondary index is warranted yet. If barrier lookup
-- becomes hot, index on (side_a, side_b) — but measure first.
