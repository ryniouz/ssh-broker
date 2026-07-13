#!/usr/bin/env python3
"""broker-cli — talk to the SSH Broker from any project (stdlib only, no deps).

Two credential modes:
  * ADMIN ops (register/list/enable/disable/logs) need --admin-token (or
    BROKER_ADMIN_TOKEN env).
  * PLUGIN ops (metrics/call/upload) need --api-key (or BROKER_API_KEY env),
    the key handed out when the plugin was registered.

Examples
--------
  # register a plugin from a capability json file, get its api key back
  broker-cli.py register --file myplugin.json

  # read metrics as a plugin
  BROKER_API_KEY=bpk_... broker-cli.py metrics

  # run a named, whitelisted command with params
  BROKER_API_KEY=bpk_... broker-cli.py call docker_pull --param image=ghcr.io/me/app:latest

  # push a file (base64'd for you) to an allowed remote path
  BROKER_API_KEY=bpk_... broker-cli.py upload ./compose.yml /mnt/user/appdata/x/compose.yml
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

DEFAULT_API = os.environ.get("BROKER_API_BASE", "http://10.11.20.1:8000")


def _req(method, base, path, headers=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(f"{base}{path}", data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main():
    ap = argparse.ArgumentParser(description="SSH Broker CLI")
    ap.add_argument("--api", default=DEFAULT_API, help="broker API base url")
    ap.add_argument("--admin-token", default=os.environ.get("BROKER_ADMIN_TOKEN", ""))
    ap.add_argument("--api-key", default=os.environ.get("BROKER_API_KEY", ""))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("register", help="create/update a plugin (admin)")
    p.add_argument("--file", required=True, help="json: {name,description,capabilities,...}")

    sub.add_parser("list", help="list plugins (admin)")

    p = sub.add_parser("enable", help="enable a plugin (admin)"); p.add_argument("name")
    p = sub.add_parser("disable", help="disable a plugin (admin)"); p.add_argument("name")

    p = sub.add_parser("logs", help="tail audit log (admin)")
    p.add_argument("--level", default=""); p.add_argument("--plugin", default="")
    p.add_argument("--limit", type=int, default=50)

    sub.add_parser("metrics", help="read host metrics (plugin)")

    p = sub.add_parser("call", help="run a named command (plugin)")
    p.add_argument("command")
    p.add_argument("--param", action="append", default=[], help="key=value (repeatable)")

    p = sub.add_parser("upload", help="push a local file (plugin)")
    p.add_argument("local"); p.add_argument("remote")

    a = ap.parse_args()
    admin_h = {"X-Admin-Token": a.admin_token}
    plug_h = {"X-API-Key": a.api_key}

    if a.cmd == "register":
        with open(a.file, encoding="utf-8") as f:
            body = json.load(f)
        st, res = _req("POST", a.api, "/plugins", admin_h, body)
    elif a.cmd == "list":
        st, res = _req("GET", a.api, "/plugins", admin_h)
    elif a.cmd in ("enable", "disable"):
        st, res = _req("POST", a.api, f"/plugins/{a.name}/{a.cmd}", admin_h)
    elif a.cmd == "logs":
        qs = f"?limit={a.limit}&level={a.level}&plugin={a.plugin}"
        st, res = _req("GET", a.api, f"/logs{qs}", admin_h)
    elif a.cmd == "metrics":
        st, res = _req("GET", a.api, "/metrics", plug_h)
    elif a.cmd == "call":
        params = dict(kv.split("=", 1) for kv in a.param)
        st, res = _req("POST", a.api, "/exec/run", plug_h, {"command": a.command, "params": params})
    elif a.cmd == "upload":
        with open(a.local, "rb") as f:
            content = base64.b64encode(f.read()).decode()
        st, res = _req("POST", a.api, "/exec/upload", plug_h,
                       {"remote_path": a.remote, "content_base64": content})
    else:
        ap.error("unknown command")

    print(json.dumps(res, indent=2))
    sys.exit(0 if 200 <= st < 300 else 1)


if __name__ == "__main__":
    main()
