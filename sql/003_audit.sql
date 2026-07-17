-- W3.4 audit schema. Hash-chained append-only log.
--
-- Every authorization decision — allow OR deny — writes a row here. Denies
-- are the compliance-interesting ones; do not filter them out.
--
-- Tamper evidence: hash = SHA256(prev_hash || canonical_json(row payload)).
-- `warden audit verify` walks the chain and reports the first sequence
-- number whose hash doesn't match. If a row is edited or removed, verify
-- fails at (or immediately after) that row.

CREATE TABLE IF NOT EXISTS audit_log (
    seq          BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    principal    TEXT NOT NULL,
    object_id    TEXT NOT NULL,
    action       TEXT NOT NULL,        -- retrieve | context_hold | cite | evict
    decision     TEXT NOT NULL,        -- allow | deny
    reason_path  JSONB NOT NULL,       -- the tuple chain — the whole point
    gate         SMALLINT NOT NULL,    -- 1 | 2 | 3
    session_id   TEXT,
    prev_hash    BYTEA NOT NULL,
    hash         BYTEA NOT NULL
);

CREATE INDEX IF NOT EXISTS audit_log_principal_ts ON audit_log (principal, ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_session_ts   ON audit_log (session_id, ts DESC);
