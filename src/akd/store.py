"""Append-only, content-hash-deduped SQLite store for captured AKD responses.

Two timestamps, because they answer different questions:

  captured_at        when this exact content FIRST appeared (holdings last CHANGED)
  last_confirmed_at  when AKD most recently re-sent it (connection last known good)

A book that has not changed is normal; deriving liveness from `captured_at`
would report it stale within 15 minutes while AKD is confirming it every few
seconds. Colliding puts therefore advance `last_confirmed_at` (never
backwards) instead of being dropped, which also makes "sell, then rebuy the
same book" and "transient blank, then real book" storable again.

The content hash is over the decoded, canonicalised payload (sorted keys), so
wire-format whitespace/ordering never reads as a change.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from . import config

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    account_ref       TEXT NOT NULL,
    operation         TEXT NOT NULL,
    captured_at       TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    payload           TEXT NOT NULL,
    UNIQUE (account_ref, operation, content_hash)
);
CREATE INDEX IF NOT EXISTS snapshots_latest_idx
    ON snapshots (account_ref, operation, last_confirmed_at DESC);
"""


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:  # naive is treated as UTC rather than rejected
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def _hash(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class SnapshotStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            config.ensure_data_dir()
        else:  # an explicit path (e.g. the portfolio command's temp store) owns only its own folder
            Path(db_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path = str(db_path or config.DB_PATH)
        # Create the file 0600 before SQLite does; its -wal/-shm files inherit the mode.
        config.write_private(Path(self._path), b"") if not Path(self._path).exists() else None
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)

    def put(self, account_ref: str, operation: str, captured_at: datetime, payload: dict) -> bool:
        """True when a NEW row was appended; False when identical content already
        existed (its `last_confirmed_at` is advanced instead)."""
        stamp = _iso(captured_at)
        try:
            with self._conn:
                self._conn.execute(
                    """INSERT INTO snapshots
                       (account_ref, operation, captured_at, last_confirmed_at, content_hash, payload)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (account_ref, operation, stamp, stamp, _hash(payload), _canonical(payload)),
                )
            return True
        except sqlite3.IntegrityError:
            with self._conn:
                self._conn.execute(
                    """UPDATE snapshots SET last_confirmed_at = MAX(last_confirmed_at, ?)
                       WHERE account_ref=? AND operation=? AND content_hash=?""",
                    (stamp, account_ref, operation, _hash(payload)),
                )
            return False

    def latest(self, account_ref: str, operation: str) -> dict | None:
        """The most recently CONFIRMED snapshot, or None if nothing was ever captured.

        `id DESC` breaks ties in favour of the row written second: timestamps can
        collide at whole-second resolution and SQLite would otherwise be free to
        return the older row."""
        row = self._conn.execute(
            """SELECT captured_at, last_confirmed_at, payload, content_hash
               FROM snapshots WHERE account_ref=? AND operation=?
               ORDER BY last_confirmed_at DESC, id DESC LIMIT 1""",
            (account_ref, operation),
        ).fetchone()
        if row is None:
            return None
        return {
            "captured_at": datetime.fromisoformat(row["captured_at"]),
            "last_confirmed_at": datetime.fromisoformat(row["last_confirmed_at"]),
            "payload": json.loads(row["payload"]),
            "content_hash": row["content_hash"],
        }

    def count(self, account_ref: str, operation: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM snapshots WHERE account_ref=? AND operation=?",
            (account_ref, operation),
        ).fetchone()
        return int(row["n"])

    def operations(self, account_ref: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT operation FROM snapshots WHERE account_ref=? ORDER BY operation",
            (account_ref,),
        ).fetchall()
        return [r["operation"] for r in rows]

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        self._conn.close()
