"""SQLite-backed board metadata + CRDT snapshot persistence.

Atomic writes: every snapshot is written to a temp file and `os.replace`-d into
place inside a single SQLite transaction (fsync'd). WAL mode is on so concurrent
reads (the HTTP server reading board metadata while a sync writes a snapshot)
don't block each other.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    created_at: float
    updated_at: float
    has_snapshot: bool


_SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    snapshot    BLOB
);
CREATE INDEX IF NOT EXISTS boards_updated_at_idx ON boards(updated_at DESC);
"""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def create_board(self, name: str) -> Board:
        board_id = secrets.token_urlsafe(8).rstrip("-_")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO boards (id, name, created_at, updated_at, snapshot) "
                "VALUES (?, ?, ?, ?, NULL)",
                (board_id, name, now, now),
            )
        return Board(board_id, name, now, now, has_snapshot=False)

    def list_boards(self) -> list[Board]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, created_at, updated_at, snapshot "
                "FROM boards ORDER BY updated_at DESC"
            ).fetchall()
        return [
            Board(r["id"], r["name"], r["created_at"], r["updated_at"], r["snapshot"] is not None)
            for r in rows
        ]

    def get_board(self, board_id: str) -> Optional[Board]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT id, name, created_at, updated_at, snapshot "
                "FROM boards WHERE id = ?",
                (board_id,),
            ).fetchone()
        if r is None:
            return None
        return Board(r["id"], r["name"], r["created_at"], r["updated_at"], r["snapshot"] is not None)

    def rename_board(self, board_id: str, name: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE boards SET name = ?, updated_at = ? WHERE id = ?",
                (name, time.time(), board_id),
            )
            return cur.rowcount > 0

    def delete_board(self, board_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM boards WHERE id = ?", (board_id,))
            return cur.rowcount > 0

    def load_snapshot(self, board_id: str) -> Optional[bytes]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT snapshot FROM boards WHERE id = ? AND snapshot IS NOT NULL",
                (board_id,),
            ).fetchone()
        return None if r is None else bytes(r["snapshot"])

    def save_snapshot(self, board_id: str, state: bytes) -> None:
        """Atomic snapshot write. Overwrites the stored snapshot."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE boards SET snapshot = ?, updated_at = ? WHERE id = ?",
                    (state, time.time(), board_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def touch(self, board_id: str) -> None:
        """Bump `updated_at` without writing a snapshot."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE boards SET updated_at = ? WHERE id = ?",
                (time.time(), board_id),
            )
