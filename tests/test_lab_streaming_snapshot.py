from __future__ import annotations

import sqlite3
import tracemalloc
from pathlib import Path

from strongwiz.canonical import canonical_bytes, content_hash
from strongwiz.lab import _ledger_snapshot, _materialized_ledger_snapshot
from strongwiz.ledger import SQLiteLedger


def _object_only_ledger(path: Path, count: int) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE objects (
                payload_hash TEXT PRIMARY KEY,
                canonical_payload BLOB NOT NULL
            );
            CREATE TABLE receipts (
                sequence INTEGER PRIMARY KEY,
                receipt_id TEXT NOT NULL UNIQUE,
                occurrence_id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                account_id TEXT NOT NULL,
                account_version INTEGER NOT NULL,
                payload_hash TEXT NOT NULL REFERENCES objects(payload_hash),
                envelope_json BLOB NOT NULL,
                receipt_hash TEXT NOT NULL UNIQUE
            );
            """
        )
        connection.executemany(
            "INSERT INTO objects(payload_hash, canonical_payload) VALUES (?, ?)",
            (
                (content_hash({"bulk": index}), canonical_bytes({"bulk": index}))
                for index in range(count)
            ),
        )


def _snapshot_peak(path: Path, count: int) -> int:
    _object_only_ledger(path, count)
    tracemalloc.start()
    try:
        snapshot = _ledger_snapshot(path)
        assert snapshot.seal.object_count == count
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def test_streaming_snapshot_matches_legacy_projection_and_terminal_closure(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "ledger.sqlite3"
    with SQLiteLedger(ledger_path) as ledger:
        terminal_ref = ledger.put_object({"source": "synthetic-domain-v1", "state": "WIN"})
        other_ref = ledger.put_object({"observation": "door-open"})
        ledger.append(
            occurrence_id="outcome-0001",
            kind="terminal_observation",
            account_id="account-1",
            account_version=0,
            payload={"concise_summary": "the declared authority returned WIN"},
            object_refs=(terminal_ref, other_ref),
        )

    _, _, expected_seal = _materialized_ledger_snapshot(ledger_path)
    snapshot = _ledger_snapshot(ledger_path, terminal_evidence_ref=terminal_ref)

    assert snapshot.seal == expected_seal
    assert snapshot.terminal_object_present
    assert snapshot.terminal_receipt_present


def test_streaming_snapshot_memory_does_not_scale_with_ledger_row_count(
    tmp_path: Path,
) -> None:
    small_peak = _snapshot_peak(tmp_path / "small.sqlite3", 20)
    large_peak = _snapshot_peak(tmp_path / "large.sqlite3", 3_000)

    assert large_peak <= small_peak + 4 * 1024 * 1024
