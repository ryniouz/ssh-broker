"""Tiny SQLite layer shared by the API and (read-only) by the web UI.

Tables:
  users    - admin accounts for the web dashboard (bcrypt hashed).
  plugins  - registered client apps + their capability grants.
  logs     - append-only audit trail of every operation through the broker.
"""
import sqlite3
import threading
import time
import json
from typing import Any, Optional

from .config import settings

_lock = threading.Lock()
_conn: Optional[sqlite3.Connection] = None


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    c = connect()
    with _lock:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plugins (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                api_key_hash TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                capabilities TEXT NOT NULL,   -- json: {"metrics": [...], "commands": {...}}
                rate_limit_per_min INTEGER NOT NULL DEFAULT 60,
                created_at REAL NOT NULL,
                last_seen REAL,
                last_ip TEXT
            );
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                plugin TEXT,
                level TEXT NOT NULL,          -- info | error | denied
                action TEXT NOT NULL,
                detail TEXT,
                ip TEXT                       -- client IP of the request, if known
            );
            """
        )
        # migrate older DBs that predate per-plugin connection tracking
        pcols = {r[1] for r in c.execute("PRAGMA table_info(plugins)")}
        if "last_ip" not in pcols:
            c.execute("ALTER TABLE plugins ADD COLUMN last_ip TEXT")
        lcols = {r[1] for r in c.execute("PRAGMA table_info(logs)")}
        if "ip" not in lcols:
            c.execute("ALTER TABLE logs ADD COLUMN ip TEXT")
        c.commit()


def q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    c = connect()
    with _lock:
        cur = c.execute(sql, args)
        rows = cur.fetchall()
        return rows


def execute(sql: str, args: tuple = ()) -> None:
    c = connect()
    with _lock:
        c.execute(sql, args)
        c.commit()


def audit(level: str, action: str, plugin: str | None = None, detail: Any = None,
          ip: str | None = None) -> None:
    if not isinstance(detail, str):
        detail = json.dumps(detail, default=str)
    execute(
        "INSERT INTO logs (ts, plugin, level, action, detail, ip) VALUES (?,?,?,?,?,?)",
        (time.time(), plugin, level, action, detail, ip),
    )
