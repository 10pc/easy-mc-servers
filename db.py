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
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS server_access (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, server_id)
            )"""
        )
        conn.commit()
        _migrate_legacy_admin(conn)


def _migrate_legacy_admin(conn):
    """One-time: single-admin hash from meta -> users table as 'admin'."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'admin_password_hash'").fetchone()
    if row is None:
        return
    exists = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
    if exists is None and conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, created_at)"
            " VALUES (?, 'admin', ?, 1, ?)",
            (f"admin-{secrets.token_hex(3)}", row["value"], time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
    conn.execute("DELETE FROM meta WHERE key = 'admin_password_hash'")
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
        srv = conn.execute(
            "SELECT id FROM servers WHERE id = ? OR name = ?", (id_or_name, id_or_name)
        ).fetchone()
        if not srv:
            return False
        conn.execute("DELETE FROM server_access WHERE server_id = ?", (srv["id"],))
        conn.execute("DELETE FROM servers WHERE id = ?", (srv["id"],))
        conn.commit()
        return True


# ---------------- users & access ----------------

def _public_user(row) -> dict:
    return {
        "id": row["id"], "username": row["username"],
        "is_admin": bool(row["is_admin"]), "created_at": row["created_at"],
    }


def list_users(include_servers: bool = False):
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        users = [_public_user(r) for r in rows]
        if include_servers:
            for u in users:
                u["servers"] = [
                    r["server_id"] for r in conn.execute(
                        "SELECT server_id FROM server_access WHERE user_id = ?", (u["id"],)
                    ).fetchall()
                ]
        return users


def get_user(username_or_id):
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ? OR username = ?", (username_or_id, username_or_id)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id):
    return get_user(user_id)


def count_admins() -> int:
    init_db()
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin = 1").fetchone()["n"]


def create_user(username: str, password_hash: str, is_admin: bool = False) -> dict:
    init_db()
    user_id = f"user-{secrets.token_hex(4)}"
    with _connect() as conn:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, username, password_hash, 1 if is_admin else 0,
             time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        conn.commit()
    u = get_user(user_id)
    return _public_user(u)


def set_user_password(user_id: str, password_hash: str):
    init_db()
    with _connect() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
        conn.commit()


def delete_user(user_id: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM server_access WHERE user_id = ?", (user_id,))
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return cur.rowcount > 0


def grant_access(user_id: str, server_id: str):
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO server_access (user_id, server_id) VALUES (?, ?)",
            (user_id, server_id),
        )
        conn.commit()


def revoke_access(user_id: str, server_id: str) -> bool:
    init_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM server_access WHERE user_id = ? AND server_id = ?", (user_id, server_id)
        )
        conn.commit()
        return cur.rowcount > 0


def user_server_ids(user_id: str) -> list:
    init_db()
    with _connect() as conn:
        return [
            r["server_id"] for r in conn.execute(
                "SELECT server_id FROM server_access WHERE user_id = ?", (user_id,)
            ).fetchall()
        ]


def user_has_access(user_id: str, server_id: str) -> bool:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM server_access WHERE user_id = ? AND server_id = ?", (user_id, server_id)
        ).fetchone()
        return row is not None


def set_password_hash(pw_hash: str):
    """Legacy compat: set-password CLI. Ensures an 'admin' admin user exists."""
    init_db()
    admin = get_user("admin")
    if admin:
        set_user_password(admin["id"], pw_hash)
    else:
        create_user("admin", pw_hash, is_admin=True)
