"""
Hash-chained audit log tests.

The tamper detection guarantee is what makes the audit log usable for
compliance. If any of these fail, do not ship.
"""

from __future__ import annotations

import pytest

from gateway.audit import (
    AuditEntry,
    InMemoryAuditLog,
    verify_chain,
)


def _entry(principal: str = "user:alice", object_id: str = "doc:d1") -> AuditEntry:
    return AuditEntry(
        principal=principal,
        object_id=object_id,
        action="retrieve",
        decision="allow",
        reason_path={"kind": "direct", "steps": []},
        gate=2,
        session_id="sess1",
    )


def test_append_populates_seq_ts_hash() -> None:
    log = InMemoryAuditLog()
    e = log.append(_entry())
    assert e.seq == 1
    assert e.ts is not None
    assert e.hash is not None and len(e.hash) == 32


def test_hash_chain_links_correctly() -> None:
    log = InMemoryAuditLog()
    e1 = log.append(_entry(object_id="doc:1"))
    e2 = log.append(_entry(object_id="doc:2"))
    e3 = log.append(_entry(object_id="doc:3"))
    assert e2.prev_hash == e1.hash
    assert e3.prev_hash == e2.hash


def test_verify_on_empty_log() -> None:
    log = InMemoryAuditLog()
    r = verify_chain(log)
    assert r.ok
    assert r.first_bad_seq is None


def test_verify_on_untouched_log() -> None:
    log = InMemoryAuditLog()
    for i in range(10):
        log.append(_entry(object_id=f"doc:{i}"))
    r = verify_chain(log)
    assert r.ok


def test_verify_detects_edited_row() -> None:
    log = InMemoryAuditLog()
    for i in range(5):
        log.append(_entry(object_id=f"doc:{i}"))
    # Tamper: someone edited row 3's decision from "allow" to "deny" to
    # cover a leak. Hash was not recomputed.
    log._entries[2].decision = "deny"
    r = verify_chain(log)
    assert not r.ok
    assert r.first_bad_seq == 3


def test_verify_detects_deleted_row() -> None:
    log = InMemoryAuditLog()
    for i in range(5):
        log.append(_entry(object_id=f"doc:{i}"))
    # Tamper: someone deleted row 3 to hide an unauthorized retrieve.
    del log._entries[2]
    # Row that was formerly row 4 now sits at index 2. Its stored prev_hash
    # references old row 3, but the row before it in the chain is now old
    # row 2 — mismatch.
    r = verify_chain(log)
    assert not r.ok


def test_verify_detects_inserted_row() -> None:
    log = InMemoryAuditLog()
    for i in range(5):
        log.append(_entry(object_id=f"doc:{i}"))
    # Tamper: someone inserted a fake row. The inserted row has neither a
    # correct prev_hash nor a correct self-hash.
    fake = _entry(object_id="doc:injected")
    fake.prev_hash = bytes(32)
    fake.hash = bytes(32)
    fake.seq = 99
    log._entries.insert(2, fake)
    r = verify_chain(log)
    assert not r.ok


def test_canonical_json_is_key_order_independent() -> None:
    """Payload canonicalization sorts keys, so two entries with different
    dict insertion order but identical content must hash the same."""
    log_a = InMemoryAuditLog()
    log_b = InMemoryAuditLog()

    e_a = AuditEntry(
        principal="user:alice",
        object_id="doc:d1",
        action="retrieve",
        decision="allow",
        reason_path={"a": 1, "b": 2},
        gate=2,
        session_id="s1",
    )
    e_b = AuditEntry(
        principal="user:alice",
        object_id="doc:d1",
        action="retrieve",
        decision="allow",
        reason_path={"b": 2, "a": 1},  # reverse order
        gate=2,
        session_id="s1",
    )
    log_a.append(e_a)
    log_b.append(e_b)
    assert e_a.hash == e_b.hash


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
