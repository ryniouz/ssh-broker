"""Runtime plugin management (admin-token protected).

This is the surface other projects use to *register themselves* or have their
capabilities adjusted, without editing files on the server. The CLI wraps these
endpoints. All calls require the `X-Admin-Token` header.
"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import db
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


@router.get("")
def list_plugins(x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    rows = db.q("SELECT id,name,description,enabled,capabilities,rate_limit_per_min,created_at,last_seen FROM plugins ORDER BY name")
    return [dict(r) for r in rows]


@router.post("")
def create_or_update(body: PluginIn, x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    created = upsert_plugin(
        name=body.name,
        description=body.description,
        capabilities=body.capabilities,
        enabled=body.enabled,
        rate_limit_per_min=body.rate_limit_per_min,
    )
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
