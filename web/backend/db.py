import os
import sqlite3
from datetime import datetime
from typing import List, Optional

DB_PATH = "/tmp/drivecam_cache.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clips (
            id          TEXT PRIMARY KEY,
            data        TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    return conn


def upsert_clips(clips: List) -> None:
    conn = _get_conn()
    now = datetime.utcnow().isoformat()
    conn.execute("BEGIN")
    try:
        for clip in clips:
            conn.execute(
                "INSERT OR REPLACE INTO clips (id, data, updated_at) VALUES (?, ?, ?)",
                (clip.id, clip.model_dump_json(), now),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_all_clips() -> List:
    from .models import Clip
    conn = _get_conn()
    rows = conn.execute("SELECT data FROM clips ORDER BY updated_at DESC").fetchall()
    return [Clip.model_validate_json(row[0]) for row in rows]


def get_cached_at() -> Optional[datetime]:
    conn = _get_conn()
    row = conn.execute("SELECT value FROM meta WHERE key = 'cached_at'").fetchone()
    if row:
        return datetime.fromisoformat(row[0])
    return None


def set_cached_at(dt: datetime) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES ('cached_at', ?, ?)",
        (dt.isoformat(), dt.isoformat()),
    )


def clear_cache() -> None:
    conn = _get_conn()
    conn.execute("DELETE FROM clips")
    conn.execute("DELETE FROM meta")