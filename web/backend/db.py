import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import List, Optional

DB_PATH = "/tmp/drivecam_cache.db"
MIN_TIMESTAMP = datetime(1970, 1, 1)


def byte_count_fmt(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


@contextmanager
def _get_conn():
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS remote_shares (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            protocol    TEXT NOT NULL,
            host        TEXT NOT NULL,
            port        INTEGER,
            username    TEXT,
            password    TEXT,
            path        TEXT NOT NULL,
            enabled     INTEGER DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        )
    """)
    try:
        yield conn
    finally:
        conn.close()


def upsert_clips(clips: List) -> None:
    now = datetime.utcnow().isoformat()
    with _get_conn() as conn:
        for clip in clips:
            conn.execute(
                "INSERT OR REPLACE INTO clips (id, data, updated_at) VALUES (?, ?, ?)",
                (clip.id, clip.model_dump_json(), now),
            )


def get_all_clips(before_timestamp: Optional[str] = None, limit: int = 200) -> tuple[List, bool, Optional[str]]:
    from .models import Clip
    with _get_conn() as conn:
        if before_timestamp:
            rows = conn.execute(
                """SELECT data FROM clips
                   WHERE json_extract(data, '$.timestamp') < ?
                   ORDER BY json_extract(data, '$.timestamp') DESC
                   LIMIT ?""",
                (before_timestamp, limit + 1),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT data FROM clips
                   ORDER BY json_extract(data, '$.timestamp') DESC
                   LIMIT ?""",
                (limit + 1,),
            ).fetchall()
        clips = [Clip.model_validate_json(row[0]) for row in rows]
        has_more = len(clips) > limit
        if has_more:
            clips = clips[:limit]
        oldest_ts = clips[-1].timestamp.isoformat() if clips and clips[-1].timestamp else None
        return clips, has_more, oldest_ts


def get_clip_count() -> int:
    with _get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM clips").fetchone()
        return row[0] if row else 0


def get_events(
    before_timestamp: Optional[str] = None,
    limit: int = 100,
    vehicle: Optional[str] = None,
    event_type: Optional[str] = None,
    folder: Optional[str] = None,
    camera: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> tuple[List[dict], bool, Optional[str]]:
    from .models import Clip
    from datetime import datetime as dt
    fetch_limit = limit * 6
    with _get_conn() as conn:
        if before_timestamp:
            rows = conn.execute(
                """SELECT data FROM clips
                   WHERE json_extract(data, '$.timestamp') < ?
                   ORDER BY json_extract(data, '$.timestamp') DESC
                   LIMIT ?""",
                (before_timestamp, fetch_limit + 1),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT data FROM clips
                   ORDER BY json_extract(data, '$.timestamp') DESC
                   LIMIT ?""",
                (fetch_limit + 1,),
            ).fetchall()
        all_clips = [Clip.model_validate_json(row[0]) for row in rows]

    by_key: dict[str, List[Clip]] = {}
    for clip in all_clips:
        key = clip.eventKey or f"none:{clip.id}"
        by_key.setdefault(key, []).append(clip)

    events = []
    for event_key, clips_in_event in by_key.items():
        clips_in_event.sort(key=lambda c: c.timestamp or MIN_TIMESTAMP, reverse=True)
        ts = clips_in_event[0].timestamp
        first = clips_in_event[0]
        events.append({
            "eventKey": event_key,
            "timestamp": ts,
            "vehicle": first.vehicle,
            "eventType": first.eventType,
            "folder": first.folder,
            "cameraAngles": sorted(set(c.cameraAngle.value for c in clips_in_event)),
            "cameraCount": len(clips_in_event),
            "totalSize": sum(c.size for c in clips_in_event),
            "sizeString": byte_count_fmt(sum(c.size for c in clips_in_event)),
            "clips": clips_in_event,
        })

    events.sort(key=lambda e: e["timestamp"] or MIN_TIMESTAMP, reverse=True)

    if vehicle and vehicle != "all":
        events = [e for e in events if e["vehicle"].value == vehicle]
    if event_type and event_type != "all":
        events = [e for e in events if e["eventType"].value == event_type]
    if folder and folder != "all":
        events = [e for e in events if e["folder"] == folder]
    if camera and camera != "all":
        events = [e for e in events if camera in e["cameraAngles"]]
    if date_from:
        try:
            df = dt.fromisoformat(date_from)
            events = [e for e in events if e["timestamp"] and e["timestamp"] >= df]
        except Exception:
            pass
    if date_to:
        try:
            dte = dt.fromisoformat(date_to)
            events = [e for e in events if e["timestamp"] and e["timestamp"] <= dte]
        except Exception:
            pass

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]
    oldest_ts = events[-1]["timestamp"].isoformat() if events and events[-1]["timestamp"] else None
    return events, has_more, oldest_ts


def get_cached_at() -> Optional[datetime]:
    with _get_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'cached_at'").fetchone()
        if row:
            return datetime.fromisoformat(row[0])
    return None


def set_cached_at(dt: datetime) -> None:
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value, updated_at) VALUES ('cached_at', ?, ?)",
            (dt.isoformat(), dt.isoformat()),
        )


def clear_cache() -> None:
    with _get_conn() as conn:
        conn.execute("DELETE FROM clips")
        conn.execute("DELETE FROM meta")


def get_remote_shares() -> List[dict]:
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT id, name, protocol, host, port, username, password, path, enabled, created_at, updated_at
            FROM remote_shares WHERE enabled = 1 ORDER BY created_at DESC
        """).fetchall()
        shares = []
        for row in rows:
            shares.append({
                "id": row[0],
                "name": row[1],
                "protocol": row[2],
                "host": row[3],
                "port": row[4],
                "username": row[5],
                "password": row[6],
                "path": row[7],
                "enabled": bool(row[8]),
                "created_at": row[9],
                "updated_at": row[10],
            })
        return shares


def get_all_remote_shares() -> List[dict]:
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT id, name, protocol, host, port, username, password, path, enabled, created_at, updated_at
            FROM remote_shares ORDER BY created_at DESC
        """).fetchall()
        shares = []
        for row in rows:
            shares.append({
                "id": row[0],
                "name": row[1],
                "protocol": row[2],
                "host": row[3],
                "port": row[4],
                "username": row[5],
                "password": row[6],
                "path": row[7],
                "enabled": bool(row[8]),
                "created_at": row[9],
                "updated_at": row[10],
            })
        return shares


def create_remote_share(
    name: str,
    protocol: str,
    host: str,
    path: str,
    port: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> int:
    now = datetime.utcnow().isoformat()
    with _get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO remote_shares (name, protocol, host, port, username, password, path, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (name, protocol, host, port, username, password, path, now, now),
        )
        return cur.lastrowid


def update_remote_share(
    share_id: int,
    name: Optional[str] = None,
    protocol: Optional[str] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    path: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> bool:
    now = datetime.utcnow().isoformat()
    fields = []
    values = []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if protocol is not None:
        fields.append("protocol = ?")
        values.append(protocol)
    if host is not None:
        fields.append("host = ?")
        values.append(host)
    if port is not None:
        fields.append("port = ?")
        values.append(port)
    if username is not None:
        fields.append("username = ?")
        values.append(username)
    if password is not None:
        fields.append("password = ?")
        values.append(password)
    if path is not None:
        fields.append("path = ?")
        values.append(path)
    if enabled is not None:
        fields.append("enabled = ?")
        values.append(1 if enabled else 0)
    if fields:
        fields.append("updated_at = ?")
        values.append(now)
        sql = f"UPDATE remote_shares SET {', '.join(fields)} WHERE id = ?"
        values.append(share_id)
        with _get_conn() as conn:
            conn.execute(sql, values)
        return True
    return False


def delete_remote_share(share_id: int) -> bool:
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM remote_shares WHERE id = ?", (share_id,))
        return cur.rowcount > 0


def test_remote_share(share_id: int) -> dict:
    from .services.smb_client import SMBClient
    from .services.ftp_client import FTPClient
    from .services.nfs_client import NFSClient
    share = None
    all_shares = get_all_remote_shares()
    for s in all_shares:
        if s["id"] == share_id:
            share = s
            break
    if not share:
        return {"success": False, "message": "Share not found"}
    try:
        if share["protocol"] == "smb":
            client = SMBClient(
                host=share["host"],
                port=share["port"] or 445,
                username=share["username"],
                password=share["password"],
                share_path=share["path"],
            )
            result = client.test_connection()
            return result
        elif share["protocol"] == "ftp":
            client = FTPClient(
                host=share["host"],
                port=share["port"] or 21,
                username=share["username"],
                password=share["password"],
            )
            result = client.test_connection(share["path"])
            return result
        elif share["protocol"] == "nfs":
            client = NFSClient(
                host=share["host"],
                port=share["port"] or 2049,
                path=share["path"],
            )
            result = client.test_connection()
            return result
        else:
            return {"success": False, "message": f"Unsupported protocol: {share['protocol']}"}
    except Exception as e:
        return {"success": False, "message": str(e)}