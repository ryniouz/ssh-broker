"""SSH Broker dashboard.

Server-rendered admin UI. Handles admin sign-up (first user) + login, then shows
plugins, host/service status, and the audit log by calling the broker API with
the shared admin token. Sessions are signed cookies (itsdangerous).
"""
import os
import time
import sqlite3
import logging

import httpx
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from passlib.hash import bcrypt

logging.basicConfig(level=logging.INFO)

API_BASE = os.environ.get("WEB_API_BASE", "http://10.11.20.1:8000")
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


# ---- tiny user store -----------------------------------------------------
def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, "
        "password_hash TEXT, created_at REAL)"
    )
    return c


def user_count() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def current_user(request: Request) -> str | None:
    return request.session.get("user")


def require_login(request: Request) -> str:
    u = current_user(request)
    if not u:
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return u


# ---- API client ----------------------------------------------------------
async def api_get(path: str, params: dict | None = None):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{API_BASE}{path}", params=params or {},
                             headers={"X-Admin-Token": ADMIN_TOKEN})
        r.raise_for_status()
        return r.json()


async def api_post(path: str):
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{API_BASE}{path}", headers={"X-Admin-Token": ADMIN_TOKEN})
        r.raise_for_status()
        return r.json()


# ---- auth routes ---------------------------------------------------------
@app.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request):
    if user_count() > 0:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse("signup.html", {"request": request})


@app.post("/signup")
async def signup(request: Request, username: str = Form(...), password: str = Form(...)):
    if user_count() > 0:
        raise HTTPException(403, "an admin already exists")
    with db() as c:
        c.execute("INSERT INTO users (username,password_hash,created_at) VALUES (?,?,?)",
                  (username, bcrypt.hash(password), time.time()))
    request.session["user"] = username
    return RedirectResponse("/", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    if user_count() == 0:
        return RedirectResponse("/signup", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row or not bcrypt.verify(password, row["password_hash"]):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"}, status_code=401)
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
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": current_user(request),
        "health": health, "plugins": plugins, "error": err,
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
        "request": request, "user": current_user(request),
        "rows": rows, "error": err, "level": level, "plugin": plugin,
    })


@app.get("/manual", response_class=HTMLResponse)
async def manual(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=302)
    import markdown as _md
    md_path = os.path.join(BASE_DIR, "manual.md")
    with open(md_path, encoding="utf-8") as f:
        html = _md.markdown(f.read(), extensions=["tables", "fenced_code"])
    return templates.TemplateResponse("manual.html", {
        "request": request, "user": current_user(request), "content": html,
    })


@app.post("/plugins/{name}/{action}")
async def plugin_action(request: Request, name: str, action: str):
    if not current_user(request):
        return RedirectResponse("/login", status_code=302)
    if action in ("enable", "disable"):
        await api_post(f"/plugins/{name}/{action}")
    return RedirectResponse("/", status_code=302)
