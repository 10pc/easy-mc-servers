<div align="center">

<pre>
                      *            (                              
                    (  `     (     )\ )                           
 (      )    (      )\))(    )\   (()/(  (  (    )     (  (       
 )\  ( /( (  )\ )  ((_)()\ (((_)   /(_))))\ )(  /((   ))\ )(  (   
((_) )(_)))\(()/(  (_()((_))\___  (_)) /((_|()\(_))\ /((_|()\ )\  
| __((_)_((_))(_)) |  \/  ((/ __| / __(_))  ((_))((_|_))  ((_|(_) 
| _|/ _` (_-< || | | |\/| || (__  \__ Y -_)| '_\ V // -_)| '_(_-< 
|___\__,_/__/\_, | |_|  |_| \___| |___|___||_|  \_/ \___||_| /__/ 
             |__/                                                 
</pre>

**an aternos-like on/off panel with e4mc tunneling. no port forwarding needed.**

</div>

> this is a tiny flask app that turns minecraft servers on and off
> from a web page. hit **start** and it boots your server, then fires up
> [e4mcbiat](https://github.com/DuncanRuns/e4mcbiat) so friends can join with
> zero port forwarding.

<div align="left">

| features |
|---|
| on/off per server |
| e4mc address + copy button |
| refresh e4mc address |
| live console + command box |
| color-coded logs |
| multi-user + per-server grants |
| zerotier join panel |
| cpu/ram/disk/uptime monitor |

</div>

## §1. quickstart

you need: python 3.12, java (for minecraft + e4mcbiat), and `cloudflared`.

```bash
python3 -m venv .venv                      # no global packages
.venv/bin/pip install -r requirements.txt
.venv/bin/python cli.py set-password       # creates your admin account
.venv/bin/python cli.py add-mcserver /path/to/server --command "java -Xmx2G -jar paper.jar nogui" --port 25565
.venv/bin/python app.py                    # dev: http://127.0.0.1:6701
```

second server? new port, matching `server.properties:server-port`:

```bash
.venv/bin/python cli.py add-mcserver ~/servers/creative --command "java -Xmx2G -jar paper.jar nogui" --port 25566
.venv/bin/python cli.py edit-server creative --port 25567
.venv/bin/python cli.py list-servers
```

## §2. secrets (.env)

secrets live in `.env` (gitignored, `chmod 600`). never in source. never in git.

```bash
ZT_NET_ID=<zerotier network id>   # enables the per-card zerotier panel; empty hides it
# ZT_IP=...                       # optional override, else auto-detected from zt* interface
# PORT=6701 BIND=127.0.0.1 SECURE_COOKIES=1
```

real environment variables override `.env` values.

## §3. how start works

1. spawns your custom `command` with `cwd=<server dir>` (`shell=False`, `shlex.split` — no shell injection).
2. waits for mc ready (`Done (...)!` in the log or the port opening, max 120s).
3. auto-downloads `e4mcbiat-*-all.jar` to `bin/` on first use, runs `java -jar bin/e4mcbiat.jar nogui port=<mc_port>`.
4. parses `Connected, domain: <domain>` out of e4mc's output, shows it with a copy button.
5. e4mc failing is non-fatal: mc stays online, the error shows on the card.

`stop` sends `stop` to the mc console, waits 30s, then SIGTERM/KILLs the process group. e4mc goes down with it. `refresh address` kills just the e4mc sidecar for a fresh domain — mc keeps running.

## §4. console + logs

every server card streams its log to the content pane. the log viewer only
auto-scrolls while you're at the bottom — scroll up to read history in peace.
lines are color-coded: <font color="red">errors</font>,
<font color="orange">warnings</font>, sent commands in cyan, dashboard noise in gray.

the command box under the viewer sends straight to the server console
(`say hi`, `time set day`, ...). everything you send is logged with a `[cmd]`
prefix so it sits right next to the server's reply.

## §5. users & auth

multi-user with roles. the first account (`admin`, via `set-password`) is admin.
only admins can add users and hand out servers — manually, via cli or the
admin panel. regular users only ever see their granted servers; anything else
404s exactly like a missing page, so there's nothing to probe.

```bash
.venv/bin/python cli.py user-add fred            # prompts password
.venv/bin/python cli.py user-add cara --admin    # second admin
.venv/bin/python cli.py grant fred survival
.venv/bin/python cli.py revoke fred survival
.venv/bin/python cli.py user-passwd fred
.venv/bin/python cli.py user-del fred
.venv/bin/python cli.py user-list
```

users can change their own password from the account box. admins can also
reset anyone's from the admin panel. deleting a user kills their sessions
instantly. sessions are signed cookies (12h, httponly, samesite=lax, secure
when `SECURE_COOKIES=1`), csrf on every POST, 5-fails/5min rate limit per ip.
no default credentials.

## §6. going live (prod)

```bash
sudo cp mc-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now mc-dashboard
cloudflared tunnel --url http://127.0.0.1:6701
```

for a permanent tunnel, set up a named cloudflared tunnel once, then add a
public hostname pointing at `http://127.0.0.1:6701` in the cloudflare
dashboard. set `SECURE_COOKIES=1` (already in the unit) since you're behind
cloudflare https.

> [!WARNING]
> flask's dev server is fine for one admin and a few friends. if this ever
> grows up, put `waitress`/`gunicorn` in front of it.

<div align="center">

### *this is vibe coded i'm sorry :P*
you can help by making issues and i'll fix them manually (real)

</div>
