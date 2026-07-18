"""
Hash-chained audit log.

Every Gate decision writes a row here — allows AND denies. Denies are the
compliance-interesting ones (they're the record that Warden said no); do
not filter them.

Hash construction:

    hash_0 = SHA256(b"\\x00" * 32 || canonical_json(row_0))
    hash_i = SHA256(hash_{i-1}   || canonical_json(row_i))

Canonical JSON: sorted keys, no whitespace, UTF-8 bytes. Anything less
strict lets a tamperer edit whitespace to keep the hash valid.

`verify_chain` walks the log and returns (ok, first_bad_seq). A missing
row shows up as a hash mismatch on the row after it, since prev_hash won't
match. An edited row shows up on ITSELF, because its computed hash no
longer matches its stored hash. Either way the pointer identifies the
tamper site.

    In-memory implementations exist for tests; production uses Postgres.
    Both go through the same `AuditWriter` Protocol so scenarios can
    exercise either.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg

# Genesis: the "prev_hash" for the very first row. Fixed 32 zero bytes so
# the chain is reproducible from empty and doesn't require a magic constant.
_GENESIS = bytes(32)


@dataclass
class AuditEntry:
    principal: str
    object_id: str
    action: str            # retrieve | context_hold | cite | evict
    decision: str          # allow | deny
    reason_path: dict[str, Any]
    gate: int              # 1 | 2 | 3
    session_id: str | None
    seq: int | None = None
    ts: datetime | None = None
    prev_hash: bytes | None = None
    hash: bytes | None = None


def _payload_bytes(entry: AuditEntry) -> bytes:
    """Canonical JSON representation of an entry's mutable payload.

    ts and seq are EXCLUDED from the payload — they're set by the storage
    layer and hashing them would make the chain non-reproducible across
    replays. We include everything the caller controls plus the resolved
    prev_hash, which fixes the ordering.
    """
    payload = {
        "principal": entry.principal,
        "object_id": entry.object_id,
        "action": entry.action,
        "decision": entry.decision,
        "reason_path": entry.reason_path,
        "gate": entry.gate,
        "session_id": entry.session_id,
        "prev_hash_hex": (entry.prev_hash or _GENESIS).hex(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _next_hash(prev_hash: bytes, entry: AuditEntry) -> bytes:
    return hashlib.sha256((prev_hash or _GENESIS) + _payload_bytes(entry)).digest()


# ---------------------------------------------------------------------------
# AuditWriter Protocol + implementations
# ---------------------------------------------------------------------------

class AuditWriter(Protocol):
    def append(self, entry: AuditEntry) -> AuditEntry: ...
    def all(self) -> list[AuditEntry]: ...


@dataclass
class InMemoryAuditLog:
    """Test-oriented log. Stores entries in a list; behaves identically to
    PostgresAuditLog for hashing purposes."""

    _entries: list[AuditEntry] = field(default_factory=list)

    def append(self, entry: AuditEntry) -> AuditEntry:
        prev = self._entries[-1].hash if self._entries else _GENESIS
        entry.seq = len(self._entries) + 1
        entry.ts = datetime.now(timezone.utc)
        entry.prev_hash = prev
        entry.hash = _next_hash(prev, entry)
        self._entries.append(entry)
        return entry

    def all(self) -> list[AuditEntry]:
        return list(self._entries)


class PostgresAuditLog:
    """Real audit log. Every append is a single INSERT with the pre-computed
    hash. The transaction discipline is the caller's — this class does not
    commit."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def append(self, entry: AuditEntry) -> AuditEntry:
        prev = self._latest_hash()
        entry.prev_hash = prev
        entry.hash = _next_hash(prev, entry)
        row = self._conn.execute(
            """
            INSERT INTO audit_log (principal, object_id, action, decision,
                                   reason_path, gate, session_id,
                                   prev_hash, hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING seq, ts
            """,
            (
                entry.principal,
                entry.object_id,
                entry.action,
                entry.decision,
                json.dumps(entry.reason_path, sort_keys=True),
                entry.gate,
                entry.session_id,
                prev,
                entry.hash,
            ),
        ).fetchone()
        entry.seq = row[0]
        entry.ts = row[1]
        return entry

    def all(self) -> list[AuditEntry]:
        rows = self._conn.execute(
            """
            SELECT seq, ts, principal, object_id, action, decision,
                   reason_path, gate, session_id, prev_hash, hash
            FROM audit_log ORDER BY seq
            """
        ).fetchall()
        return [_row_to_entry(r) for r in rows]

    def _latest_hash(self) -> bytes:
        row = self._conn.execute(
            "SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else _GENESIS


def _row_to_entry(r: tuple) -> AuditEntry:
    reason_path = r[6]
    if isinstance(reason_path, str):
        reason_path = json.loads(reason_path)
    return AuditEntry(
        seq=r[0],
        ts=r[1],
        principal=r[2],
        object_id=r[3],
        action=r[4],
        decision=r[5],
        reason_path=reason_path,
        gate=r[7],
        session_id=r[8],
        prev_hash=bytes(r[9]),
        hash=bytes(r[10]),
    )


# ---------------------------------------------------------------------------
# Chain verification
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    ok: bool
    first_bad_seq: int | None  # None if ok

    def __bool__(self) -> bool:
        return self.ok


def verify_chain(log: AuditWriter) -> VerifyResult:
    """Recompute the hash chain end-to-end. Returns (ok, first_bad_seq).

    Detects three tamper modes:
      - Row edited in place → its recomputed hash doesn't match its stored hash.
      - Row deleted → the next row's stored prev_hash doesn't match the
        previous row's stored hash.
      - Row inserted → same as edit; the inserted row's hash won't match.
    """
    prev = _GENESIS
    for entry in log.all():
        if entry.prev_hash != prev:
            return VerifyResult(ok=False, first_bad_seq=entry.seq)
        computed = _next_hash(prev, entry)
        if computed != entry.hash:
            return VerifyResult(ok=False, first_bad_seq=entry.seq)
        prev = entry.hash
    return VerifyResult(ok=True, first_bad_seq=None)
