"""SSH Broker dashboard (v1.1).

Server-rendered admin UI:
  * First sign-up becomes the admin (active). Later sign-ups are PENDING and
    must be approved by an admin under "Users".
  * Admin-only tabs: Users (approve/deny) and Settings (acquire the SSH key).
  * Dashboard / Logs / Manual as before.

Passwords are hashed with bcrypt directly (no passlib). Sessions are signed
cookies. The broker API is reached with the shared admin token.
"""
import os
import time
import sqlite3
import logging

import bcrypt
import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

logging.basicConfig(level=logging.INFO)

API_BASE = os.environ.get("WEB_API_BASE", "http://127.0.0.1:8000")
ADMIN_TOKEN = os.environ.get("WEB_ADMIN_TOKEN", "")
SESSION_SECRET = os.environ.get("WEB_SESSION_SECRET", "change-me-in-env")
DB_PATH = os.environ.get("WEB_DB_PATH", "/data/web.db")

BASE_DIR = os.path.dirname(__file__)
app = FastAPI(title="SSH Broker Dashboard")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def _fromjson(s):
    import json
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


def _fmtts(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts))) if ts else ""


templates.env.filters["fromjson"] = _fromjson
templates.env.filters["ago"] = _ago
templates.env.filters["fmtts"] = _fmtts


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
    # migrate older schemas that predate the approval system
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    if "is_admin" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    if "status" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'active'")
    c.commit()
    return c


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
        r = await client.post(f"{API_BASE}{path}", json=json_body,
                              headers={"X-Admin-Token": ADMIN_TOKEN})
        return r


def cloud_instructions(admin_token: str | None) -> str:
    """Self-contained brief for a cloud/AI agent to onboard a plugin + drive the broker."""
    tok = admin_token or "<ADMIN_API_KEY — ask an admin>"
    api = API_BASE
    return f"""SSH BROKER — INSTRUCTIONS FOR A CLOUD / AI AGENT
=================================================
You are connecting an app to an SSH Broker. The broker holds ONE SSH connection
to a host and exposes a small HTTP API. Your app never gets shell access — it
gets a scoped API key that can read specific metrics and run specific named,
whitelisted commands.

BASE URL:     {api}
ADMIN KEY:    {tok}      (header: X-Admin-Token — for registering plugins)
PLUGIN KEY:   returned once when you register a plugin (header: X-API-Key)

STEP 1 — Register a plugin (once). A "plugin" declares what your app may do.
  Commands are templates with $param placeholders; each param is regex-checked
  then shell-quoted. Docker '{{{{.X}}}}' and awk '$8' pass through untouched.

  curl -s -X POST {api}/plugins \\
    -H "X-Admin-Token: {tok}" -H "Content-Type: application/json" \\
    -d '{{
      "name": "my-app",
      "description": "what my app does",
      "rate_limit_per_min": 60,
      "capabilities": {{
        "metrics": ["cpu","ram","disk"],
        "commands": {{
          "docker_pull": {{"template":"docker pull $image","timeout":300,
            "params":{{"image":{{"pattern":"^[a-zA-Z0-9._/:@-]+$","required":true}}}}}},
          "docker_ps": {{"template":"docker ps --format '{{{{.Names}}}}\\t{{{{.Status}}}}'","params":{{}}}}
        }},
        "upload": {{"path_prefix":"/mnt/user/appdata/"}}
      }}
    }}'
  Response includes "api_key":"bpk_..."  — SAVE IT. It is shown only once.

STEP 2 — Read host metrics (uses the plugin key):
  curl -s {api}/metrics -H "X-API-Key: bpk_..."

STEP 3 — Run a whitelisted command by NAME (never raw shell):
  curl -s -X POST {api}/exec/run -H "X-API-Key: bpk_..." \\
    -H "Content-Type: application/json" \\
    -d '{{"command":"docker_pull","params":{{"image":"ghcr.io/me/app:latest"}}}}'

STEP 4 — Push a file over SFTP (if your plugin has "upload"):
  curl -s -X POST {api}/exec/upload -H "X-API-Key: bpk_..." \\
    -H "Content-Type: application/json" \\
    -d '{{"remote_path":"/mnt/user/appdata/x/compose.yml","content_base64":"<base64>"}}'

RULES
  - To add a NEW capability, update the plugin (POST /plugins again with the same
    name and the fuller capabilities block) using the admin key.
  - A command not in the plugin's grant returns 403. A bad param returns 400.
  - If you get 503 "not configured", an admin must acquire the SSH key first
    (Settings -> Acquire SSH key in the web UI).
  - Auth: X-Admin-Token manages plugins; X-API-Key is per-app.
"""


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
    # pending: do not log in
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
    with db() as c:
        pending = c.execute("SELECT COUNT(*) n FROM users WHERE status='pending'").fetchone()["n"]
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": current_user(request), "is_admin": is_admin(request),
        "health": health, "plugins": plugins, "error": err, "pending": pending,
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
        "rows": rows, "error": err, "level": level, "plugin": plugin,
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
        "cloud_text": cloud_instructions(ADMIN_TOKEN if admin else None),
    })


# ---- admin: user management ----------------------------------------------
@app.get("/users", response_class=HTMLResponse)
async def users_view(request: Request):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    with db() as c:
        rows = c.execute("SELECT id,username,is_admin,status,created_at FROM users ORDER BY status='pending' DESC, created_at").fetchall()
    return templates.TemplateResponse("users.html", {
        "request": request, "user": current_user(request), "is_admin": True, "rows": rows,
    })


@app.post("/users/{uid}/{action}")
async def user_action(request: Request, uid: int, action: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    me = current_user(request)
    with db() as c:
        target = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if target and target["username"] != me["username"]:  # never act on yourself
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
        "admin_token": ADMIN_TOKEN,
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
    # "test" checks the login can reach the host and stores nothing;
    # "acquire" installs the broker's key. The password is forwarded once and
    # never stored by the web layer.
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


@app.post("/plugins/{name}/{action}")
async def plugin_action(request: Request, name: str, action: str):
    if not is_admin(request):
        return RedirectResponse("/login", status_code=302)
    if action in ("enable", "disable"):
        await api_post(f"/plugins/{name}/{action}")
    return RedirectResponse("/", status_code=302)
