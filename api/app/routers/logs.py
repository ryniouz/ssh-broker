"""Read-only audit log access for the web dashboard (admin-token protected)."""
from fastapi import APIRouter, Header, HTTPException

from .. import db
from ..config import settings

router = APIRouter(prefix="/logs", tags=["logs"])


def _require_admin(x_admin_token: str) -> None:
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "invalid admin token")


@router.get("")
def recent(limit: int = 200, level: str = "", plugin: str = "", x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    sql = "SELECT ts,plugin,level,action,detail,ip FROM logs WHERE 1=1"
    args: list = []
    if level:
        sql += " AND level=?"
        args.append(level)
    if plugin:
        sql += " AND plugin=?"
        args.append(plugin)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 1000))
    return [dict(r) for r in db.q(sql, tuple(args))]
