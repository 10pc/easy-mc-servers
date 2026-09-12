"""SQLite storage for mc-dashboard. Stdlib only."""
import secrets
import sqlite3
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "servers.db"


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS servers (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                directory TEXT UNIQUE NOT NULL,
                command TEXT NOT NULL,
                mc_port INTEGER UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        conn.commit()


def _new_id(name: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    slug = "-".join(filter(None, slug.split("-")))[:24] or "server"
    return f"{slug}-{secrets.token_hex(3)}"


def list_servers():
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM servers ORDER BY name").fetchall()
        return [dict(r) for r in rows]


def get_server(id_or_name):
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM servers WHERE id = ? OR name = ?", (id_or_name, id_or_name)
        ).fetchone()
        return dict(row) if row else None


def port_in_use_by_other(port: int, exclude_id: str | None = None) -> bool:
    init_db()
    with _connect() as conn:
        if exclude_id:
            row = conn.execute(
                "SELECT id FROM servers WHERE mc_port = ? AND id != ?", (port, exclude_id)
            ).fetchone()
        else:
            row = conn.execute("SELECT id FROM servers WHERE mc_port = ?", (port,)).fetchone()
        return row is not None


def add_server(name: str, directory: str, command: str, mc_port: int) -> dict:
    init_db()
    server_id = _new_id(name)
    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO servers (id, name, directory, command, mc_port, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (server_id, name, directory, command, mc_port, created),
        )
        conn.commit()
    return get_server(server_id)


def update_server(server_id: str, **fields) -> dict | None:
    init_db()
    allowed = {"name", "directory", "command", "mc_port"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_server(server_id)
    cols = ", ".join(f"{k} = ?" for k in updates)
    with _connect() as conn:
        conn.execute(f"UPDATE servers SET {cols} WHERE id = ?", (*updates.values(), server_id))
        conn.commit()
    return get_server(server_id)


def remove_server(id_or_name) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM servers WHERE id = ? OR name = ?", (id_or_name, id_or_name)
        )
        conn.commit()
        return cur.rowcount > 0


def get_password_hash() -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = 'admin_password_hash'").fetchone()
        return row["value"] if row else None


def set_password_hash(pw_hash: str):
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('admin_password_hash', ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (pw_hash,),
        )
        conn.commit()
