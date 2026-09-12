"""Flask dashboard: single-admin auth, per-server start/stop, e4mc domain + logs."""
import os
import secrets
import time
from datetime import timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

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
        if not session.get("authed"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper


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
    pw_hash = db.get_password_hash()
    if request.method == "GET":
        if session.get("authed"):
            return redirect(url_for("index"))
        return render_template("login.html", no_password=(pw_hash is None))
    ip = _client_ip()
    wait = _rate_blocked(ip)
    if wait > 0:
        return render_template("login.html", error=f"Too many attempts, try again in {int(wait)}s.",
                               no_password=False), 429
    if pw_hash is None:
        return render_template("login.html", error="No admin password set. Run: .venv/bin/python cli.py set-password",
                               no_password=True), 503
    password = request.form.get("password", "")
    if check_password_hash(pw_hash, password):
        _attempts.pop(ip, None)
        session.clear()
        session["authed"] = True
        session.permanent = True
        _csrf_token()
        return redirect(url_for("index"))
    _record_fail(ip)
    time.sleep(0.5)  # slow brute force slightly
    return render_template("login.html", error="Invalid password.", no_password=False), 401


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
    return render_template("index.html", csrf_token=_csrf_token())


@app.route("/api/servers")
@login_required
def api_servers():
    servers = db.list_servers()
    return jsonify([manager.get_state(s) for s in servers])


@app.route("/api/servers/<sid>/logs")
@login_required
def api_logs(sid):
    server = db.get_server(sid)
    if not server:
        return jsonify({"error": "not found"}), 404
    return jsonify({"logs": manager.tail_log(server["id"], max_lines=300)})


@app.route("/api/servers/<sid>/start", methods=["POST"])
@login_required
def api_start(sid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    server = db.get_server(sid)
    if not server:
        return jsonify({"error": "not found"}), 404
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
    server = db.get_server(sid)
    if not server:
        return jsonify({"error": "not found"}), 404
    state = manager.stop(server)
    return jsonify(state)


@app.route("/api/servers/<sid>/regenerate", methods=["POST"])
@login_required
def api_regenerate(sid):
    if not _check_csrf():
        return jsonify({"error": "bad csrf"}), 403
    server = db.get_server(sid)
    if not server:
        return jsonify({"error": "not found"}), 404
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
    server = db.get_server(sid)
    if not server:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        state = manager.send_command(server, data.get("command", ""))
        return jsonify(state)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409


if __name__ == "__main__":
    db.init_db()
    # Bind loopback only: exposed via Cloudflare Tunnel, no port forwarding.
    app.run(host=BIND, port=PORT, threaded=True)
