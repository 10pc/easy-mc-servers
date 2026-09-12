"""Flask dashboard: multi-user auth, per-server start/stop, e4mc domain + logs + console."""
import os
import secrets
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db
import manager as mgr_mod

BASE_DIR = Path(__file__).resolve().parent
SECRET_FILE = BASE_DIR / ".secret_key"

PORT = int(os.getenv("PORT", "6701"))
BIND = os.getenv("BIND", "127.0.0.1")
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "0") == "1"


def _secret_key() -> str:
    if SECRET_FILE.exists():
        return SECRET_FILE.read_text().strip()
    key = secrets.token_hex(32)
    SECRET_FILE.write_text(key)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return key


app = Flask(__name__)
app.secret_key = _secret_key()
app.permanent_session_lifetime = timedelta(hours=12)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SECURE_COOKIES,
)

manager = mgr_mod.ServerManager()

# --- simple in-memory login rate limiting ---
_attempts: dict = {}  # ip -> {fails, first, blocked_until}
MAX_FAILS = 5
WINDOW = 300
BLOCK = 300


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()


def _rate_blocked(ip: str) -> float:
    info = _attempts.get(ip)
    if not info:
        return 0
    now = time.time()
    if info.get("blocked_until", 0) > now:
        return info["blocked_until"] - now
    if now - info.get("first", now) > WINDOW:
        _attempts.pop(ip, None)
    return 0


def _record_fail(ip: str):
    now = time.time()
    info = _attempts.get(ip)
    if not info or now - info.get("first", now) > WINDOW:
        info = {"fails": 0, "first": now, "blocked_until": 0}
    info["fails"] += 1
    if info["fails"] >= MAX_FAILS:
        info["blocked_until"] = now + BLOCK
    _attempts[ip] = info


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if _current_user() is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        user = _current_user()
        if user is None:
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        if not user["is_admin"]:
            return jsonify({"error": "forbidden"}), 403
        return fn(*a, **kw)
    return wrapper


def _current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    user = db.get_user_by_id(uid)
    if not user:
        session.clear()
        return None
    return {"id": user["id"], "username": user["username"], "is_admin": bool(user["is_admin"])}


def _server_for(user, sid):
    """Return (server, None) if visible, else (None, error_status).
    Unauthorized servers look identical to missing ones (404)."""
    server = db.get_server(sid)
    if not server:
        return None, 404
    if user["is_admin"] or db.user_has_access(user["id"], server["id"]):
        return server, None
    return None, 404


def _visible_servers(user):
    servers = db.list_servers()
    if user["is_admin"]:
        return servers
    allowed = set(db.user_server_ids(user["id"]))
    return [s for s in servers if s["id"] in allowed]


def _csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_hex(16)
        session["csrf"] = tok
    return tok


def _check_csrf() -> bool:
    want = session.get("csrf")
    got = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
    return bool(want and got and secrets.compare_digest(str(want), str(got)))


@app.route("/login", methods=["GET", "POST"])
def login():
    db.init_db()
    has_users = len(db.list_users()) > 0
    if request.method == "GET":
        if _current_user():
            return redirect(url_for("index"))
        return render_template("login.html", no_password=not has_users)
    ip = _client_ip()
    wait = _rate_blocked(ip)
    if wait > 0:
        return render_template("login.html", error=f"Too many attempts, try again in {int(wait)}s.",
                               no_password=False), 429
    if not has_users:
        return render_template("login.html", error="No users yet. Run: .venv/bin/python cli.py set-password",
                               no_password=True), 503
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "")
    user = db.get_user(username) if username else None
    if user and check_password_hash(user["password_hash"], password):
        _attempts.pop(ip, None)
        session.clear()
        session["user_id"] = user["id"]
        session["is_admin"] = bool(user["is_admin"])
        session.permanent = True
        _csrf_token()
        return redirect(url_for("index"))
    _record_fail(ip)
    time.sleep(0.5)  # slow brute force slightly
    return render_template("login.html", error="Invalid username or password.", no_password=False), 401


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    user = _current_user()
    return render_template("index.html", csrf_token=_csrf_token(),
                           is_admin=user["is_admin"], username=user["username"])


@app.route("/api/servers")
@login_required
def api_servers():
    return jsonify([manager.get_state(s) for s in _visible_servers(_current_user())])


@app.route("/api/servers/<sid>/logs")
@login_required
def api_logs(sid):
    server, err = _server_for(_current_user(), sid)
    if err:
        return jsonify({"error": "not found"}), err
    return jsonify({"logs": manager.tail_log(server["id"], max_lines=300)})


@app.route("/api/servers/<sid>/start", methods=["POST"])
@login_required
def api_start(sid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    server, err = _server_for(_current_user(), sid)
    if err:
        return jsonify({"error": "not found"}), err
    try:
        state = manager.start(server)
        return jsonify(state)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/servers/<sid>/stop", methods=["POST"])
@login_required
def api_stop(sid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    server, err = _server_for(_current_user(), sid)
    if err:
        return jsonify({"error": "not found"}), err
    state = manager.stop(server)
    return jsonify(state)


@app.route("/api/servers/<sid>/regenerate", methods=["POST"])
@login_required
def api_regenerate(sid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    server, err = _server_for(_current_user(), sid)
    if err:
        return jsonify({"error": "not found"}), err
    try:
        state = manager.restart_e4mc(server)
        return jsonify(state)
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


@app.route("/api/servers/<sid>/console", methods=["POST"])
@login_required
def api_console(sid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    server, err = _server_for(_current_user(), sid)
    if err:
        return jsonify({"error": "not found"}), err
    data = request.get_json(silent=True) or {}
    try:
        state = manager.send_command(server, data.get("command", ""))
        return jsonify(state)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


# ---------------- admin: users & grants (admin only) ----------------

def _valid_username(name: str) -> str | None:
    name = (name or "").strip().lower()
    if 3 <= len(name) <= 32 and all(c.isalnum() or c in "-_" for c in name):
        return name
    return None


@app.route("/api/admin/users")
@admin_required
def api_admin_users():
    users = db.list_users(include_servers=True)
    servers = {s["id"]: s["name"] for s in db.list_servers()}
    out = []
    for u in users:
        out.append({
            "id": u["id"], "username": u["username"], "is_admin": u["is_admin"],
            "created_at": u["created_at"],
            "servers": [{"id": sid, "name": servers.get(sid, sid)} for sid in u["servers"]],
        })
    return jsonify({"users": out, "servers": [{"id": s["id"], "name": s["name"]}
                                              for s in db.list_servers()]})


@app.route("/api/admin/users", methods=["POST"])
@admin_required
def api_admin_create_user():
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    data = request.get_json(silent=True) or {}
    username = _valid_username(data.get("username", ""))
    password = data.get("password", "")
    if not username:
        return jsonify({"error": "username must be 3-32 chars of letters, digits, - or _"}), 400
    if len(password) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400
    if db.get_user(username):
        return jsonify({"error": "user already exists"}), 409
    user = db.create_user(username, generate_password_hash(password),
                           is_admin=bool(data.get("is_admin")))
    return jsonify(user), 201


@app.route("/api/admin/users/<uid>", methods=["DELETE"])
@admin_required
def api_admin_delete_user(uid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    user = db.get_user_by_id(uid)
    if not user:
        return jsonify({"error": "not found"}), 404
    if uid == session.get("user_id"):
        return jsonify({"error": "cannot delete yourself"}), 400
    if user["is_admin"] and db.count_admins() <= 1:
        return jsonify({"error": "cannot delete the last admin"}), 400
    db.delete_user(uid)
    return jsonify({"ok": True})


@app.route("/api/admin/users/<uid>/password", methods=["POST"])
@admin_required
def api_admin_reset_password(uid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    user = db.get_user_by_id(uid)
    if not user:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    if len(data.get("password", "")) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400
    db.set_user_password(uid, generate_password_hash(data["password"]))
    return jsonify({"ok": True})


@app.route("/api/admin/grants", methods=["POST"])
@admin_required
def api_admin_grant():
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    data = request.get_json(silent=True) or {}
    user = db.get_user_by_id(data.get("user_id", ""))
    server = db.get_server(data.get("server_id", ""))
    if not user or not server:
        return jsonify({"error": "unknown user or server"}), 404
    if user["is_admin"]:
        return jsonify({"error": "admins already see all servers"}), 400
    db.grant_access(user["id"], server["id"])
    return jsonify({"ok": True})


@app.route("/api/admin/grants", methods=["DELETE"])
@admin_required
def api_admin_revoke():
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    data = request.get_json(silent=True) or {}
    if not db.revoke_access(data.get("user_id", ""), data.get("server_id", "")):
        return jsonify({"error": "no such grant"}), 404
    return jsonify({"ok": True})


if __name__ == "__main__":
    db.init_db()
    # Bind loopback only: exposed via Cloudflare Tunnel, no port forwarding.
    app.run(host=BIND, port=PORT, threaded=True)
