"""SSH Broker dashboard.

Server-rendered admin UI:
  * First sign-up becomes the admin; later sign-ups need approval (Users).
  * Settings: test / acquire / revoke the broker's SSH key.
  * Plugins: create, view detail (status, IP, per-plugin logs, usage), edit,
    enable/disable, rotate key, delete.
  * Manual: admin key + "Instruction for Claude" (build + use plugins).

Passwords are hashed with bcrypt directly. Sessions are signed cookies. The
broker API is reached with the shared admin token.
"""
import os
import json
import time
import sqlite3
import logging
from urllib.parse import quote
from datetime import datetime
from zoneinfo import ZoneInfo

import bcrypt
import httpx
from fastapi import FastAPI, Request, Form, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(level=logging.INFO)

API_BASE = os.environ.get("WEB_API_BASE", "http://127.0.0.1:8000")
ADMIN_TOKEN = os.environ.get("WEB_ADMIN_TOKEN", "")
SESSION_SECRET = os.environ.get("WEB_SESSION_SECRET", "change-me-in-env")
DB_PATH = os.environ.get("WEB_DB_PATH", "/data/web.db")
APP_VERSION = "1.3.3"


class NoCacheStaticFiles(StaticFiles):
    """Browsers/WebViews can cache static assets aggressively enough that a
    redeploy silently keeps serving old CSS/JS -- this project has hit that
    exact bug before (personal-appstore). Force revalidation on every asset;
    templates also cache-bust the URL itself with ?v={{ APP_VERSION }}."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


BASE_DIR = os.path.dirname(__file__)
app = FastAPI(title="SSH Broker Dashboard")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", NoCacheStaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _fromjson(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except Exception:  # noqa: BLE001
        return {}


def _ago(ts):
    if not ts:
        return "never"
    d = time.time() - float(ts)
    if d < 60:
        return f"{int(d)}s ago"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


DISPLAY_TZ = ZoneInfo(os.environ.get("WEB_DISPLAY_TZ", "America/Vancouver"))


def _fmtts(ts):
    if not ts:
        return ""
    # server timestamps are stored as UTC epoch seconds; render in the
    # operator's local timezone rather than the container's (usually UTC)
    return datetime.fromtimestamp(float(ts), tz=DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def _recent(ts, secs: int = 120) -> bool:
    return bool(ts) and (time.time() - float(ts)) < secs


def _pretty(obj) -> str:
    if isinstance(obj, str):
        obj = _fromjson(obj)
    return json.dumps(obj, indent=2)


def _fmttime(ts):
    """Just the clock time (local tz) — used inside cards where the row's own
    'x ago' already conveys recency, so the full date would be noise."""
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts), tz=DISPLAY_TZ).strftime("%H:%M:%S")


def _fmtdetail(detail):
    """Log detail is stored as a JSON string for dict payloads and a plain
    string otherwise. Render it as compact, readable text rather than raw JSON
    braces so the card stays scannable."""
    if detail in (None, "", "null"):
        return ""
    obj = detail
    if isinstance(detail, str):
        stripped = detail.strip()
        if stripped[:1] in "{[":
            try:
                obj = json.loads(stripped)
            except Exception:  # noqa: BLE001
                return detail
        else:
            return detail
    if isinstance(obj, dict):
        return "  ".join(f"{k}: {v}" for k, v in obj.items())
    return str(obj)


def group_logs(rows):
    """Collapse consecutive entries that are the same event (plugin+level+
    action) into one card with a count and a time span. A page of logs is
    mostly the same few repeated calls (a poller hitting /metrics, say); one
    card that says 'metrics x40, 10:01-10:07' reads far better on a phone than
    40 identical single-line table rows."""
    groups = []
    for r in rows:
        r = dict(r)
        key = (r.get("plugin"), r.get("level"), r.get("action"))
        if groups and groups[-1]["_key"] == key:
            g = groups[-1]
            g["count"] += 1
            g["first_ts"] = r.get("ts")  # rows arrive newest-first, so this walks back in time
            # keep the newest non-empty detail/ip as representative
            if not g.get("detail") and _fmtdetail(r.get("detail")):
                g["detail"] = _fmtdetail(r.get("detail"))
            if not g.get("ip") and r.get("ip"):
                g["ip"] = r.get("ip")
        else:
            groups.append({
                "_key": key,
                "plugin": r.get("plugin"),
                "level": r.get("level"),
                "action": r.get("action"),
                "ip": r.get("ip"),
                "detail": _fmtdetail(r.get("detail")),
                "last_ts": r.get("ts"),
                "first_ts": r.get("ts"),
                "count": 1,
            })
    return groups


templates.env.filters["fromjson"] = _fromjson
templates.env.filters["ago"] = _ago
templates.env.filters["fmtts"] = _fmtts
templates.env.filters["fmttime"] = _fmttime
templates.env.filters["recent"] = _recent
templates.env.filters["pretty"] = _pretty
templates.env.globals["APP_VERSION"] = APP_VERSION


# ---- password hashing (bcrypt, 72-byte safe) -----------------------------
def hash_pw(p: str) -> str:
    return bcrypt.hashpw(p.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def verify_pw(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode("utf-8")[:72], h.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---- user store ----------------------------------------------------------
def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE IF NOT EXISTS users ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, "
        "password_hash TEXT, is_admin INTEGER DEFAULT 0, "
        "status TEXT DEFAULT 'pending', created_at REAL)"
    )
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    if "is_admin" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    if "status" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    # per-plugin instruction / README docs uploaded by an admin
    c.execute(
        "CREATE TABLE IF NOT EXISTS plugin_docs ("
        "name TEXT PRIMARY KEY, filename TEXT, content TEXT, updated_at REAL)"
    )
    c.commit()
    return c


MAX_DOC_BYTES = 256 * 1024  # cap uploaded README size


def get_doc(name: str):
    with db() as c:
        return c.execute("SELECT * FROM plugin_docs WHERE name=?", (name,)).fetchone()


def set_doc(name: str, filename: str, content: str) -> None:
    with db() as c:
        c.execute(
            "INSERT INTO plugin_docs (name,filename,content,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET filename=excluded.filename, "
            "content=excluded.content, updated_at=excluded.updated_at",
            (name, filename, content, time.time()),
        )


def del_doc(name: str) -> None:
    with db() as c:
        c.execute("DELETE FROM plugin_docs WHERE name=?", (name,))


async def _read_upload(upload: "UploadFile | None") -> tuple[str, str] | None:
    """Return (filename, text) for a non-empty uploaded doc.

    Returns None only when no file was attached at all (a legitimate "skip" for
    the optional readme field at plugin creation). Anything the admin actually
    tried to upload but that isn't usable raises ValueError with a message the
    caller shows back to them — a silent no-op here is exactly what made this
    bug hard to spot (upload "succeeds", nothing ever gets stored)."""
    if upload is None or not upload.filename:
        return None
    raw = await upload.read(MAX_DOC_BYTES + 1)
    if len(raw) > MAX_DOC_BYTES:
        raise ValueError(f"'{upload.filename}' is too large (max 256 KB)")
    if b"\x00" in raw:
        raise ValueError(f"'{upload.filename}' doesn't look like a text file — upload a .md or .txt")
    text = raw.decode("utf-8", "replace")
    if not text.strip():
        raise ValueError(f"'{upload.filename}' is empty")
    return (os.path.basename(upload.filename), text)


def user_count() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def get_user(username: str):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()


def current_user(request: Request):
    u = request.session.get("user")
    return get_user(u) if u else None


def is_admin(request: Request) -> bool:
    u = current_user(request)
    return bool(u and u["is_admin"])


# ---- API client ----------------------------------------------------------
async def api_get(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{API_BASE}{path}", params=params or {},
                             headers={"X-Admin-Token": ADMIN_TOKEN})
        r.raise_for_status()
        return r.json()


async def api_post(path: str, json_body: dict | None = None, timeout: float = 30):
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(f"{API_BASE}{path}", json=json_body,
                                 headers={"X-Admin-Token": ADMIN_TOKEN})


async def api_delete(path: str, timeout: float = 15):
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request("DELETE", f"{API_BASE}{path}",
                                    headers={"X-Admin-Token": ADMIN_TOKEN})


# ---- instruction generators ----------------------------------------------
CLAUDE_TEMPLATE = r"""INSTRUCTIONS FOR CLAUDE — SSH Broker
====================================
You are wiring an app up to an SSH Broker. The broker keeps ONE SSH connection
to a host and exposes a small HTTP API. Your app never gets a shell — it gets a
scoped API key that can read whitelisted data and run whitelisted commands.

ENDPOINTS
  Base URL   : %API%
  Admin key  : %TOK%        (header  X-Admin-Token  — creates/edits plugins)
  Plugin key : returned once when a plugin is created/rotated (header X-API-Key)

────────────────────────────────────────────────────────────────────────────
1. THE MODEL: metrics vs commands
   • metrics   = a small fixed set of cached host stats: cpu, ram, disk.
   • commands  = named, whitelisted shell templates you define. THIS is how you
                 pull anything else — GPU load, temperatures, container state,
                 package versions, etc. If the data isn't a built-in metric,
                 add a command that runs the query.

2. BUILDING A PLUGIN (capability schema)
   {
     "name": "my-app",                      // unique, [a-z0-9-]
     "description": "what my app does",
     "enabled": true,
     "rate_limit_per_min": 60,              // requests/min before 429
     "capabilities": {
       "metrics": ["cpu","ram","disk"],     // any of cpu|ram|disk (omit if none)
       "commands": {
         "<command_name>": {
           "template": "some cmd $arg",      // $param placeholders (see rules)
           "timeout": 60,                    // seconds (optional, default 60)
           "params": {
             "arg": { "pattern": "^[A-Za-z0-9._/:-]+$", "required": true }
           }
         }
       },
       "upload": { "path_prefix": "/mnt/user/appdata/" }   // optional SFTP grant
     }
   }

   TEMPLATE RULES (important)
   • Use $name or ${name} for parameters. Every value is regex-checked against
     its "pattern" then shell-quoted, so an app can't inject shell.
   • Docker's {{.Field}} and awk's $8 are NOT parameters — they pass through
     untouched. Only $word tokens are substituted.
   • A param with no matching "pattern" match is rejected (400). Keep patterns
     as tight as possible.

3. EXAMPLES (mix and match into one plugin's "commands")
   Docker:
     "docker_ps":   { "template": "docker ps --format '{{.Names}}\t{{.Status}}'", "params": {} }
     "docker_pull": { "template": "docker pull $image", "timeout": 300,
                      "params": { "image": { "pattern": "^[a-zA-Z0-9._/:@-]+$", "required": true } } }
   GPU (NVIDIA) — utilisation, memory, temperature:
     "gpu": { "template": "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits",
              "params": {} }
     "gpu_procs": { "template": "nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader", "params": {} }
   Sensors / disk health:
     "temps":  { "template": "sensors -u", "params": {} }
     "smart":  { "template": "smartctl -A $dev", "params": { "dev": { "pattern": "^/dev/[a-z0-9]+$", "required": true } } }
   Free-form read (still constrained by params):
     "tail_log": { "template": "tail -n $lines $path",
                   "params": { "lines": {"pattern":"^[0-9]{1,4}$","required":true},
                               "path":  {"pattern":"^/mnt/user/[A-Za-z0-9._/-]+$","required":true} } }

4. REGISTER THE PLUGIN (admin key). Returns the plugin api_key ONCE.
   curl -s -X POST %API%/plugins -H "X-Admin-Token: %TOK%" \
     -H "Content-Type: application/json" -d @plugin.json
   # -> {"status":"created","id":"pl_...","api_key":"bpk_..."}   SAVE the api_key.

5. USE THE PLUGIN while developing / running your app (plugin key):
   Read metrics:
     curl -s %API%/metrics -H "X-API-Key: bpk_..."
   Run a command by NAME (never raw shell):
     curl -s -X POST %API%/exec/run -H "X-API-Key: bpk_..." \
       -H "Content-Type: application/json" \
       -d '{"command":"gpu","params":{}}'
   Push a file (needs "upload"):
     curl -s -X POST %API%/exec/upload -H "X-API-Key: bpk_..." \
       -H "Content-Type: application/json" \
       -d '{"remote_path":"/mnt/user/appdata/app/x.conf","content_base64":"<b64>"}'

6. UPDATING a plugin later: POST /plugins again with the SAME name and the fuller
   capabilities block (admin key). Existing api_key is preserved. To mint a new
   key use POST /plugins/<name>/rotate-key. To remove it: DELETE /plugins/<name>.

7. AFTER YOU BUILD A PLUGIN — GENERATE ITS INSTRUCTION FILE.
   Save a file in your app repo, e.g. PLUGIN_<name>.md, containing:
     • the base URL and the plugin's api_key (X-API-Key) + admin key note,
     • every command name with its params and a ready curl example,
     • which metrics/upload the plugin may use.
   The broker's web UI generates exactly this text on the plugin's page and when
   the key is created/rotated — copy it verbatim into that file so the app (and
   the next Claude session) knows how to call the plugin.

RULES / ERRORS
  • X-Admin-Token manages plugins; X-API-Key is per app.
  • 403 = command/metric not granted · 400 = bad param · 429 = rate limited ·
    503 = broker has no SSH key yet (an admin must Acquire one in Settings).
"""


def claude_instructions(admin_token: str | None) -> str:
    tok = admin_token or "<ADMIN_API_KEY — ask an admin>"
    return CLAUDE_TEMPLATE.replace("%API%", API_BASE).replace("%TOK%", tok)


def plugin_usage(p: dict, key: str) -> str:
    """Per-plugin instruction: how to call THIS plugin, with its key + auth."""
    caps = _fromjson(p.get("capabilities"))
    api = API_BASE
    L = [f"PLUGIN: {p.get('name','')}"]
    if p.get("description"):
        L.append(p["description"])
    L += ["", "AUTH",
          f"  Base URL : {api}",
          f"  Header   : X-API-Key: {key}",
          f"  Rate     : {p.get('rate_limit_per_min', 60)}/min",
          f"  Admin key (to edit this plugin, header X-Admin-Token): {ADMIN_TOKEN}", ""]
    metrics = caps.get("metrics") or []
    if metrics:
        L += [f"METRICS (families: {', '.join(metrics)})",
              f"  curl -s {api}/metrics -H \"X-API-Key: {key}\"", ""]
    commands = caps.get("commands") or {}
    if commands:
        L.append("COMMANDS")
        for cname, spec in commands.items():
            params = spec.get("params") or {}
            example = {k: f"<{k}>" for k in params}
            L.append(f"  • {cname}   template: {spec.get('template', '')}")
            L.append(f"    params: {', '.join(params.keys()) or '(none)'}")
            L.append(f"    curl -s -X POST {api}/exec/run -H \"X-API-Key: {key}\" "
                     f"-H \"Content-Type: application/json\" \\")
            L.append(f"      -d '{json.dumps({'command': cname, 'params': example})}'")
        L.append("")
    up = caps.get("upload")
    if up:
        prefix = up.get("path_prefix", "/tmp/")
        L += [f"UPLOAD (files under {prefix})",
              f"  curl -s -X POST {api}/exec/upload -H \"X-API-Key: {key}\" "
              f"-H \"Content-Type: application/json\" \\",
              f"      -d '{{\"remote_path\":\"{prefix}file\",\"content_base64\":\"<b64>\"}}'", ""]
    L += ["NOTES",
          "  - Call commands by NAME only; raw shell is never accepted.",
          "  - 403 not granted · 400 bad param · 429 rate limited · 503 no SSH key yet."]
    return "\n".join(L)


# ---- auth routes ---------------------------------------------------------
@app.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request):
    first = user_count() == 0
    return templates.TemplateResponse("signup.html", {"request": request, "first": first, "error": None})


@app.post("/signup")
async def signup(request: Request, username: str = Form(...), password: str = Form(...)):
    if get_user(username):
        return templates.TemplateResponse("signup.html",
            {"request": request, "first": user_count() == 0, "error": "That username is taken"}, status_code=409)
    first = user_count() == 0
    with db() as c:
        c.execute(
            "INSERT INTO users (username,password_hash,is_admin,status,created_at) VALUES (?,?,?,?,?)",
            (username, hash_pw(password), 1 if first else 0, "active" if first else "pending", time.time()),
        )
    if first:
        request.session["user"] = username
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("pending.html", {"request": request, "username": username})


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if user_count() == 0:
        return RedirectResponse("/signup", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    row = get_user(username)
    if not row or not verify_pw(password, row["password_hash"]):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
    if row["status"] != "active":
        return templates.TemplateResponse("login.html",
            {"request": request, "error": "Your account is pending admin approval."}, status_code=403)
    request.session["user"] = username
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ---- dashboard -----------------------------------------------------------
async def _pending_count() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM users WHERE status='pending'").fetchone()["n"]


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=302)
    health, plugins, err = None, [], None
    try:
        health = await api_get("/health")
        plugins = await api_get("/plugins")
    except Exception as e:  # noqa: BLE001
        err = str(e)
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": current_user(request), "is_admin": is_admin(request),
        "health": health, "plugins": plugins, "error": err, "pending": await _pending_count(),
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_view(request: Request, level: str = "", plugin: str = ""):
    if not current_user(request):
        return RedirectResponse("/login", status_code=302)
    rows, err = [], None
    try:
        rows = await api_get("/logs", {"limit": 300, "level": level, "plugin": plugin})
    except Exception as e:  # noqa: BLE001
        err = str(e)
    return templates.TemplateResponse("logs.html", {
        "request": request, "user": current_user(request), "is_admin": is_admin(request),
        "log_groups": group_logs(rows), "log_count": len(rows), "error": err, "level": level, "plugin": plugin,
    })


@app.get("/manual", response_class=HTMLResponse)
async def manual(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=302)
    admin = is_admin(request)
    import markdown as _md
    with open(os.path.join(BASE_DIR, "manual.md"), encoding="utf-8") as f:
        html = _md.markdown(f.read(), extensions=["tables", "fenced_code"])
    return templates.TemplateResponse("manual.html", {
        "request": request, "user": current_user(request), "is_admin": admin, "content": html,
        "admin_token": ADMIN_TOKEN if admin else None,
        "claude_text": claude_instructions(ADMIN_TOKEN if admin else None),
    })


# ---- admin: plugins ------------------------------------------------------
def _admin_ctx(request, **extra):
    ctx = {"request": request, "user": current_user(request), "is_admin": True,
           "pending": 0}
    ctx.update(extra)
    return ctx


@app.get("/plugins/new", response_class=HTMLResponse)
async def plugin_new_form(request: Request):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    sample = json.dumps({
        "metrics": ["cpu", "ram", "disk"],
        "commands": {
            "gpu": {"template": "nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits", "params": {}},
        },
        "upload": {"path_prefix": "/mnt/user/appdata/"},
    }, indent=2)
    return templates.TemplateResponse("plugin_new.html",
        _admin_ctx(request, error=None, form={"capabilities": sample, "rate": 60}))


@app.post("/plugins/new", response_class=HTMLResponse)
async def plugin_new(request: Request, name: str = Form(...), description: str = Form(""),
                     rate_limit_per_min: int = Form(60), capabilities_json: str = Form("{}"),
                     readme: UploadFile | None = File(None)):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    form = {"name": name, "description": description, "rate": rate_limit_per_min, "capabilities": capabilities_json}
    try:
        caps = json.loads(capabilities_json or "{}")
    except json.JSONDecodeError as e:
        return templates.TemplateResponse("plugin_new.html",
            _admin_ctx(request, error=f"Capabilities is not valid JSON: {e}", form=form))
    try:
        doc = await _read_upload(readme)
    except ValueError as e:
        return templates.TemplateResponse("plugin_new.html", _admin_ctx(request, error=str(e), form=form))
    r = await api_post("/plugins", {"name": name, "description": description,
                                    "capabilities": caps, "rate_limit_per_min": int(rate_limit_per_min)})
    if r.status_code != 200:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        return templates.TemplateResponse("plugin_new.html", _admin_ctx(request, error=str(detail), form=form))
    data = r.json()
    if doc:
        set_doc(name, doc[0], doc[1])
    if data.get("status") != "created":
        # name already existed -> it was updated; go to its page
        return RedirectResponse(f"/plugins/{name}", status_code=302)
    p = await api_get(f"/plugins/{name}")
    return templates.TemplateResponse("plugin_created.html",
        _admin_ctx(request, p=p, api_key=data["api_key"], usage=plugin_usage(p, data["api_key"]), rotated=False))


@app.get("/plugins/{name}", response_class=HTMLResponse)
async def plugin_detail(request: Request, name: str, doc_err: str = "", doc_ok: str = ""):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    try:
        p = await api_get(f"/plugins/{name}")
    except Exception as e:  # noqa: BLE001
        return templates.TemplateResponse("plugin_detail.html",
            _admin_ctx(request, p=None, error=f"Plugin not found: {e}", logs=[], usage=""))
    logs = []
    try:
        logs = await api_get("/logs", {"plugin": name, "limit": 200})
    except Exception:  # noqa: BLE001
        pass
    import markdown as _md
    doc = get_doc(name)
    doc_html = _md.markdown(doc["content"], extensions=["tables", "fenced_code"]) if doc else None
    return templates.TemplateResponse("plugin_detail.html",
        _admin_ctx(request, p=p, error=None, log_groups=group_logs(logs), log_count=len(logs),
                   usage=plugin_usage(p, "<PLUGIN_API_KEY>"),
                   doc=doc, doc_html=doc_html, doc_err=doc_err or None, doc_ok=doc_ok or None))


@app.post("/plugins/{name}/edit")
async def plugin_edit(request: Request, name: str, description: str = Form(""),
                      rate_limit_per_min: int = Form(60), capabilities_json: str = Form("{}"),
                      enabled: str = Form("")):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    try:
        caps = json.loads(capabilities_json or "{}")
    except json.JSONDecodeError:
        return RedirectResponse(f"/plugins/{name}?err=badjson", status_code=302)
    await api_post("/plugins", {"name": name, "description": description, "capabilities": caps,
                                "rate_limit_per_min": int(rate_limit_per_min), "enabled": enabled == "on"})
    return RedirectResponse(f"/plugins/{name}", status_code=302)


@app.post("/plugins/{name}/enable")
async def plugin_enable(request: Request, name: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    await api_post(f"/plugins/{name}/enable")
    return RedirectResponse(f"/plugins/{name}", status_code=302)


@app.post("/plugins/{name}/disable")
async def plugin_disable(request: Request, name: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    await api_post(f"/plugins/{name}/disable")
    return RedirectResponse(f"/plugins/{name}", status_code=302)


@app.post("/plugins/{name}/rotate", response_class=HTMLResponse)
async def plugin_rotate(request: Request, name: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    r = await api_post(f"/plugins/{name}/rotate-key")
    if r.status_code != 200:
        return RedirectResponse(f"/plugins/{name}", status_code=302)
    key = r.json()["api_key"]
    p = await api_get(f"/plugins/{name}")
    return templates.TemplateResponse("plugin_created.html",
        _admin_ctx(request, p=p, api_key=key, usage=plugin_usage(p, key), rotated=True))


@app.post("/plugins/{name}/readme")
async def plugin_readme(request: Request, name: str, readme: UploadFile = File(...)):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    try:
        doc = await _read_upload(readme)
    except ValueError as e:
        return RedirectResponse(f"/plugins/{name}?doc_err={quote(str(e))}", status_code=302)
    if not doc:
        # this route requires a file; an empty/no file selection is an error here,
        # not a silent skip (that's only valid for the optional field at creation)
        return RedirectResponse(f"/plugins/{name}?doc_err={quote('No file was selected')}", status_code=302)
    set_doc(name, doc[0], doc[1])
    return RedirectResponse(f"/plugins/{name}?doc_ok={quote(doc[0])}", status_code=302)


@app.post("/plugins/{name}/readme/delete")
async def plugin_readme_delete(request: Request, name: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    del_doc(name)
    return RedirectResponse(f"/plugins/{name}", status_code=302)


@app.post("/plugins/{name}/delete")
async def plugin_delete(request: Request, name: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    await api_delete(f"/plugins/{name}")
    del_doc(name)
    return RedirectResponse("/", status_code=302)


# ---- admin: user management ----------------------------------------------
@app.get("/users", response_class=HTMLResponse)
async def users_view(request: Request):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    with db() as c:
        rows = c.execute("SELECT id,username,is_admin,status,created_at FROM users ORDER BY status='pending' DESC, created_at").fetchall()
    return templates.TemplateResponse("users.html",
        _admin_ctx(request, rows=rows, pending=await _pending_count()))


@app.post("/users/{uid}/{action}")
async def user_action(request: Request, uid: int, action: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    me = current_user(request)
    with db() as c:
        target = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if target and target["username"] != me["username"]:
            if action == "approve":
                c.execute("UPDATE users SET status='active' WHERE id=?", (uid,))
            elif action == "deny":
                c.execute("DELETE FROM users WHERE id=?", (uid,))
    return RedirectResponse("/users", status_code=302)


# ---- admin: settings / acquire SSH key -----------------------------------
async def _ssh_status():
    try:
        return await api_get("/ssh/status")
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def _settings_ctx(request, status, **extra):
    ctx = {
        "request": request, "user": current_user(request), "is_admin": True,
        "status": status, "result": None, "test_result": None, "error": None,
        "admin_token": ADMIN_TOKEN, "pending": 0,
    }
    ctx.update(extra)
    return ctx


@app.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    status = await _ssh_status()
    return templates.TemplateResponse("settings.html", _settings_ctx(request, status))


@app.post("/settings/acquire", response_class=HTMLResponse)
async def settings_acquire(request: Request, host: str = Form(...), username: str = Form(...),
                           password: str = Form(...), port: int = Form(22), intent: str = Form("acquire")):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    endpoint = "/ssh/test" if intent == "test" else "/ssh/acquire"
    result_key = "test_result" if intent == "test" else "result"
    out, error = None, None
    try:
        r = await api_post(endpoint,
                           {"host": host, "username": username, "password": password, "port": port},
                           timeout=45)
        if r.status_code == 200:
            out = r.json()
        else:
            error = r.json().get("detail", r.text)
    except Exception as e:  # noqa: BLE001
        error = str(e)
    status = await _ssh_status()
    return templates.TemplateResponse("settings.html",
        _settings_ctx(request, status, **{result_key: out, "error": error}))


@app.post("/settings/revoke", response_class=HTMLResponse)
async def settings_revoke(request: Request):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    error = None
    try:
        r = await api_post("/ssh/revoke", timeout=20)
        if r.status_code != 200:
            error = r.json().get("detail", r.text)
    except Exception as e:  # noqa: BLE001
        error = str(e)
    status = await _ssh_status()
    return templates.TemplateResponse("settings.html", _settings_ctx(request, status, error=error))
