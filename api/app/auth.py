"""Plugin authentication + authorization + simple in-memory rate limiting.

Apps authenticate to the broker with an API key sent as `X-API-Key`. The key is
only ever stored hashed (sha256). Each plugin carries a capability grant that
decides which metrics it may read and which named commands it may run.
"""
import hashlib
import secrets
import time
import json
from collections import defaultdict, deque

from fastapi import Header, HTTPException

from . import db


def new_api_key() -> tuple[str, str]:
    """Return (plaintext_key, sha256_hash). Plaintext is shown to the user once."""
    key = "bpk_" + secrets.token_urlsafe(32)
    return key, hash_key(key)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


# plugin_id -> deque[timestamps] within the trailing 60s window
_hits: dict[str, deque] = defaultdict(deque)


def _rate_ok(plugin_id: str, limit: int) -> bool:
    now = time.time()
    dq = _hits[plugin_id]
    while dq and now - dq[0] > 60:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


async def require_plugin(x_api_key: str = Header(default="")) -> dict:
    """FastAPI dependency: resolve + authorize the calling plugin."""
    if not x_api_key:
        raise HTTPException(401, "missing X-API-Key")
    rows = db.q("SELECT * FROM plugins WHERE api_key_hash=?", (hash_key(x_api_key),))
    if not rows:
        db.audit("denied", "auth", detail="bad api key")
        raise HTTPException(401, "invalid api key")
    p = dict(rows[0])
    if not p["enabled"]:
        db.audit("denied", "auth", plugin=p["name"], detail="plugin disabled")
        raise HTTPException(403, "plugin disabled")
    if not _rate_ok(p["id"], p["rate_limit_per_min"]):
        db.audit("denied", "rate_limit", plugin=p["name"])
        raise HTTPException(429, "rate limit exceeded")
    db.execute("UPDATE plugins SET last_seen=? WHERE id=?", (time.time(), p["id"]))
    p["capabilities"] = json.loads(p["capabilities"])
    return p


def can_metric(plugin: dict, metric: str) -> bool:
    return metric in plugin["capabilities"].get("metrics", [])


def command_spec(plugin: dict, name: str) -> dict | None:
    return plugin["capabilities"].get("commands", {}).get(name)
