#!/usr/bin/env python3
"""CLI: add-mcserver <directory> --command ... --port ... --name ..."""
import argparse
import getpass
import sys
from pathlib import Path

from werkzeug.security import generate_password_hash

import db


def _resolve_dir(raw: str) -> str:
    p = Path(raw).expanduser().resolve()
    if not p.exists():
        sys.exit(f"error: directory does not exist: {raw}")
    if not p.is_dir():
        sys.exit(f"error: not a directory: {raw}")
    return str(p)


def _validate_port(port: int, exclude_id=None):
    if not 1024 <= port <= 65535:
        sys.exit("error: --port must be 1024-65535")
    if db.port_in_use_by_other(port, exclude_id):
        sys.exit(f"error: port {port} already claimed by another server (use edit-server --port to change)")


def cmd_set_password(_args):
    pw = getpass.getpass("New admin password (min 12 chars recommended): ")
    pw2 = getpass.getpass("Confirm: ")
    if pw != pw2:
        sys.exit("error: passwords do not match")
    if len(pw) < 8:
        sys.exit("error: password must be at least 8 characters")
    if len(pw) < 12:
        print("warning: short password, 12+ chars recommended", file=sys.stderr)
    db.set_password_hash(generate_password_hash(pw))
    print("admin password set")


def cmd_add(a):
    directory = _resolve_dir(a.directory)
    name = a.name or Path(directory).name
    if not a.command:
        sys.exit("error: --command is required, e.g. --command 'java -Xmx2G -jar paper.jar nogui'")
    if db.get_server(name):
        sys.exit(f"error: name already exists: {name}")
    _validate_port(a.port)
    # friendly warnings (non-fatal)
    jars = list(Path(directory).glob("*.jar"))
    if not jars:
        print(f"warning: no .jar found in {directory}", file=sys.stderr)
    props = Path(directory) / "server.properties"
    if props.exists():
        try:
            text = props.read_text(errors="replace")
            for line in text.splitlines():
                if line.strip().startswith("server-port="):
                    try:
                        cfg_port = int(line.split("=", 1)[1].strip())
                        if cfg_port != a.port:
                            print(f"warning: server.properties server-port={cfg_port} != --port {a.port}",
                                  file=sys.stderr)
                    except ValueError:
                        pass
        except OSError:
            pass
    if not (Path(directory) / "eula.txt").exists():
        print("hint: accept the Mojang EULA (eula=true in eula.txt) or the server will exit", file=sys.stderr)
    srv = db.add_server(name=name, directory=directory, command=a.command, mc_port=a.port)
    print(f"added {srv['name']} (id={srv['id']}) dir={srv['directory']} port={srv['mc_port']}")


def cmd_edit(a):
    srv = db.get_server(a.id_or_name)
    if not srv:
        sys.exit(f"error: server not found: {a.id_or_name}")
    fields = {}
    if a.name:
        if a.name != srv["name"] and db.get_server(a.name):
            sys.exit(f"error: name already exists: {a.name}")
        fields["name"] = a.name
    if a.directory:
        fields["directory"] = _resolve_dir(a.directory)
    if a.command:
        fields["command"] = a.command
    if a.port is not None:
        _validate_port(a.port, exclude_id=srv["id"])
        fields["mc_port"] = a.port
    if not fields:
        sys.exit("error: nothing to change (use --name/--directory/--command/--port)")
    print("note: stop the server before changing port/command, change applies on next start")
    updated = db.update_server(srv["id"], **fields)
    print(f"updated {updated['name']} port={updated['mc_port']}")


def cmd_remove(a):
    if db.remove_server(a.id_or_name):
        print("removed")
    else:
        sys.exit(f"error: server not found: {a.id_or_name}")


def cmd_list(_a):
    rows = db.list_servers()
    if not rows:
        print("no servers yet. usage: add-mcserver <directory> --command ... --port 25565")
        return
    for r in rows:
        print(f"{r['name']}\tport={r['mc_port']}\tdir={r['directory']}\n  id={r['id']}\n  cmd={r['command']}")


def main():
    ap = argparse.ArgumentParser(prog="mc-dashboard cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("set-password", help="set admin dashboard password")
    p.set_defaults(fn=cmd_set_password)

    # add-mcserver is the headline command; also accept add alias
    p = sub.add_parser("add-mcserver", aliases=["add"], help="add a minecraft server directory")
    p.add_argument("directory", help="directory of a minecraft server")
    p.add_argument("--name", default=None, help="display name (default: folder name)")
    p.add_argument("--command", required=True, help="custom start command, e.g. 'java -Xmx2G -jar paper.jar nogui'")
    p.add_argument("--port", type=int, default=25565, help="local MC port (default 25565, must be unique)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("edit-server", aliases=["edit"], help="change name/dir/command/port")
    p.add_argument("id_or_name")
    p.add_argument("--name", default=None)
    p.add_argument("--directory", default=None)
    p.add_argument("--command", default=None)
    p.add_argument("--port", type=int, default=None)
    p.set_defaults(fn=cmd_edit)

    p = sub.add_parser("remove-server", aliases=["remove", "rm"], help="remove entry (does not delete files)")
    p.add_argument("id_or_name")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("list-servers", aliases=["list", "ls"], help="list entries")
    p.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
