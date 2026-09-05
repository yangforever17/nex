"""A transactional, idempotent *local publication sink*, not an HTTP wrapper."""

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Any, Iterator


class PublicationConflict(RuntimeError):
    """A logical ID was reused for a different payload."""


class PublicationLedger:
    """The SQLite insertion IS the effect; no external callback runs here.

    Deduplication survives restarts of this ledger and concurrent insertions.
    This does not atomically couple SQLite with arbitrary remote side effects.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS publications (logical_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                yield connection
        finally:
            connection.close()

    def commit(self, logical_id: str, payload: dict[str, Any]) -> bool:
        if not isinstance(logical_id, str) or not logical_id or len(logical_id) > 512:
            raise ValueError("logical_id must contain 1–512 characters")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        # BEGIN IMMEDIATE serializes read/compare/insert across connections.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT payload FROM publications WHERE logical_id = ?", (logical_id,)).fetchone()
            if previous is not None:
                if previous[0] != serialized:
                    raise PublicationConflict("logical ID already committed with a different payload")
                return False
            db.execute("INSERT INTO publications VALUES (?, ?)", (logical_id, serialized))
        return True

    def records(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT logical_id, payload FROM publications ORDER BY logical_id").fetchall()
        return [{"logical_id": key, "payload": json.loads(payload)} for key, payload in rows]
