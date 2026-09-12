# MC Dashboard (Aternos-like on/off + e4mcbiat)

Simple Flask dashboard. Binds `127.0.0.1` only — expose via Cloudflare Tunnel, no port forwarding.

## Setup (venv, no global packages)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python cli.py set-password
.venv/bin/python cli.py add-mcserver /path/to/server --command "java -Xmx2G -jar paper.jar nogui" --port 25565
.venv/bin/python app.py              # dev: http://127.0.0.1:6701
```

Secrets live in `.env` (gitignored, `chmod 600`), never in source:

```bash
ZT_NET_ID=<zerotier network id>   # enables the per-card zerotier panel; empty hides it
# ZT_IP=...                       # optional override, else auto-detected from zt* interface
# PORT=6701 BIND=127.0.0.1 SECURE_COOKIES=1
```

Real environment variables override `.env` values.

Second instance: new port, matching `server.properties:server-port`:

```bash
.venv/bin/python cli.py add-mcserver ~/servers/creative --command "java -Xmx2G -jar paper.jar nogui" --port 25566
.venv/bin/python cli.py edit-server creative --port 25567
.venv/bin/python cli.py list-servers
```

## How Start works

1. Spawns your custom `command` with `cwd=<server dir>` (`shell=False`, `shlex.split`).
2. Waits for MC ready (`Done (...)!` log line or port open, max 120s).
3. Auto-downloads `e4mcbiat-*-all.jar` to `bin/` on first use, runs `java -jar bin/e4mcbiat.jar nogui port=<mc_port>`.
4. Parses `Connected, domain: <domain>` from e4mc output, shows it with a copy button.
5. e4mc failure is non-fatal: MC stays online, error shown on card.

Stop sends `stop` to the MC console, waits 30s, then SIGTERM/KILLs the process group; e4mc is stopped too.

## Auth & users

Multi-user with roles. The first account (`admin`, created via `set-password`) is admin.
Only admins can manage users, and only via CLI or the admin panel at the bottom
of the dashboard. Regular users only see servers explicitly granted to them
(everything else returns 404, indistinguishable from missing).

```bash
.venv/bin/python cli.py user-add fred            # add user (prompts password)
.venv/bin/python cli.py user-add cara --admin    # add second admin
.venv/bin/python cli.py grant fred survival      # give fred a server
.venv/bin/python cli.py revoke fred survival
.venv/bin/python cli.py user-passwd fred
.venv/bin/python cli.py user-del fred
.venv/bin/python cli.py user-list
```

Sessions: signed cookie (12h, HttpOnly, SameSite=Lax, Secure when
`SECURE_COOKIES=1`), CSRF token on POST, 5-fails/5min rate limit per IP.
No default credentials. Deleting a user instantly invalidates their sessions.

## Prod

```bash
sudo cp mc-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mc-dashboard
cloudflared tunnel --url http://127.0.0.1:6701
```

Set `SECURE_COOKIES=1` (already in the unit) when serving HTTPS via Cloudflare.
