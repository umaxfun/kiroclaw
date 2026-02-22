"""C3: Session Store — SQLite-backed mapping of Telegram threads to Kiro sessions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class SessionRecord:
    """A stored mapping between a Telegram thread and a Kiro session."""

    user_id: int
    thread_id: int
    session_id: str
    workspace_path: str
    model: str


class SessionStore:
    """Persist (user_id, thread_id) → kiro_session_id mappings in SQLite.

    Database file: ./tg-acp.db (or custom path).
    Schema created on init. Connection stays open until close().
    """

    def __enter__(self) -> SessionStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __init__(self, db_path: str) -> None:
        # NOTE: This connection is only safe from the asyncio event loop thread.
        # If accessed from a thread pool executor, pass check_same_thread=False.
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    def get_session(self, user_id: int, thread_id: int) -> SessionRecord | None:
        """Lookup session by (user_id, thread_id). Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? AND thread_id = ?",
            (user_id, thread_id),
        ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            user_id=row["user_id"],
            thread_id=row["thread_id"],
            session_id=row["session_id"],
            workspace_path=row["workspace_path"],
            model=row["model"],
        )

    def upsert_session(
        self, user_id: int, thread_id: int, session_id: str, workspace_path: str
    ) -> None:
        """Create or replace session mapping. Resets model to 'auto' on replace."""
        now = _now_iso()
        self._conn.execute(
            """INSERT INTO sessions
               (user_id, thread_id, session_id, workspace_path, model, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'auto', ?, ?)
               ON CONFLICT(user_id, thread_id) DO UPDATE SET
                   session_id = excluded.session_id,
                   workspace_path = excluded.workspace_path,
                   model = 'auto',
                   updated_at = excluded.updated_at""",
            (user_id, thread_id, session_id, workspace_path, now, now),
        )
        self._conn.commit()

    def set_model(self, user_id: int, thread_id: int, model: str) -> None:
        """Update model selection for a thread. No-op if row doesn't exist."""
        self._conn.execute(
            "UPDATE sessions SET model = ?, updated_at = ? WHERE user_id = ? AND thread_id = ?",
            (model, _now_iso(), user_id, thread_id),
        )
        self._conn.commit()

    def get_model(self, user_id: int, thread_id: int) -> str:
        """Get model for thread. Returns 'auto' if no row exists."""
        row = self._conn.execute(
            "SELECT model FROM sessions WHERE user_id = ? AND thread_id = ?",
            (user_id, thread_id),
        ).fetchone()
        if row is None:
            return "auto"
        return row["model"]

    def delete_session(self, user_id: int, thread_id: int) -> None:
        """Delete session record. Used for stale lock recovery (BR-07)."""
        self._conn.execute(
            "DELETE FROM sessions WHERE user_id = ? AND thread_id = ?",
            (user_id, thread_id),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
                user_id        INTEGER NOT NULL,
                thread_id      INTEGER NOT NULL,
                session_id     TEXT    NOT NULL,
                workspace_path TEXT    NOT NULL,
                model          TEXT    NOT NULL DEFAULT 'auto',
                created_at     TEXT    NOT NULL,
                updated_at     TEXT    NOT NULL,
                PRIMARY KEY (user_id, thread_id)
            )"""
        )
        self._conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_workspace_dir(workspace_base_path: str, user_id: int, thread_id: int) -> str:
    """Create workspace directory for a thread. Returns absolute path."""
    path = Path(workspace_base_path) / str(user_id) / str(thread_id)
    path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())
