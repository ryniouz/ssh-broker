"""Runtime plugin management (admin-token protected).

This is the surface other projects use to *register themselves* or have their
capabilities adjusted, without editing files on the server. The CLI wraps these
endpoints. All calls require the `X-Admin-Token` header.
"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import db, auth
from ..config import settings
from ..plugin_loader import upsert_plugin

router = APIRouter(prefix="/plugins", tags=["plugins"])


def _require_admin(x_admin_token: str = Header(default="")) -> None:
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "invalid admin token")


class PluginIn(BaseModel):
    name: str
    description: str = ""
    capabilities: dict = {}
    enabled: bool = True
    rate_limit_per_min: int = 60


_COLS = "id,name,description,enabled,capabilities,rate_limit_per_min,created_at,last_seen,last_ip"


@router.get("")
def list_plugins(x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    rows = db.q(f"SELECT {_COLS} FROM plugins ORDER BY name")
    return [dict(r) for r in rows]


@router.get("/{name}")
def get_plugin(name: str, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    rows = db.q(f"SELECT {_COLS} FROM plugins WHERE name=?", (name,))
    if not rows:
        raise HTTPException(404, "plugin not found")
    return dict(rows[0])


@router.delete("/{name}")
def delete_plugin(name: str, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    if not db.q("SELECT 1 FROM plugins WHERE name=?", (name,)):
        raise HTTPException(404, "plugin not found")
    db.execute("DELETE FROM plugins WHERE name=?", (name,))
    db.audit("info", "plugin_delete", plugin=name)
    return {"status": "deleted", "name": name}


@router.post("/{name}/rotate-key")
def rotate_key(name: str, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    if not db.q("SELECT 1 FROM plugins WHERE name=?", (name,)):
        raise HTTPException(404, "plugin not found")
    key, key_hash = auth.new_api_key()
    db.execute("UPDATE plugins SET api_key_hash=? WHERE name=?", (key_hash, name))
    db.audit("info", "plugin_rotate_key", plugin=name)
    # plaintext key returned exactly once
    return {"status": "rotated", "name": name, "api_key": key}


@router.post("")
def create_or_update(body: PluginIn, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    try:
        created = upsert_plugin(
            name=body.name,
            description=body.description,
            capabilities=body.capabilities,
            enabled=body.enabled,
            rate_limit_per_min=body.rate_limit_per_min,
        )
    except ValueError as e:
        raise HTTPException(400, f"invalid capabilities: {e}")
    if created:
        # api_key returned exactly once, at creation
        return {"status": "created", **created}
    return {"status": "updated", "name": body.name}


@router.post("/{name}/disable")
def disable(name: str, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    db.execute("UPDATE plugins SET enabled=0 WHERE name=?", (name,))
    db.audit("info", "plugin_disable", plugin=name)
    return {"status": "disabled", "name": name}


@router.post("/{name}/enable")
def enable(name: str, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    db.execute("UPDATE plugins SET enabled=1 WHERE name=?", (name,))
    db.audit("info", "plugin_enable", plugin=name)
    return {"status": "enabled", "name": name}
