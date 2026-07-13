"""System endpoints: one-time SSH key acquisition + SSH status (admin token)."""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from .. import db
from ..config import settings
from ..ssh_manager import manager

router = APIRouter(prefix="/ssh", tags=["system"])


def _require_admin(x_admin_token: str) -> None:
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(401, "invalid admin token")


class AcquireRequest(BaseModel):
    host: str
    username: str
    password: str
    port: int = 22


@router.post("/acquire")
async def acquire(body: AcquireRequest, x_admin_token: str = Header(default="")):
    """Use a one-time password to install the broker's own key on the target.
    The password is used for this single call only and is never persisted."""
    _require_admin(x_admin_token)
    try:
        result = await manager.acquire(
            host=body.host, user=body.username, password=body.password, port=body.port
        )
    except Exception as e:  # noqa: BLE001
        # deliberately do NOT include the password anywhere in the log
        db.audit("error", "ssh_acquire", detail={"host": body.host, "user": body.username, "error": str(e)})
        raise HTTPException(400, f"acquire failed: {e}")
    db.audit("info", "ssh_acquire", detail={"host": body.host, "user": body.username})
    return result


@router.post("/test")
async def test(body: AcquireRequest, x_admin_token: str = Header(default="")):
    """Check the credentials can reach the host. Stores nothing."""
    _require_admin(x_admin_token)
    try:
        result = await manager.test_login(
            host=body.host, user=body.username, password=body.password, port=body.port
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"login test failed: {e}")
    db.audit("info", "ssh_test", detail={"host": body.host, "user": body.username})
    return result


@router.post("/revoke")
async def revoke(x_admin_token: str = Header(default="")):
    """Delete the stored key + target; the broker becomes unconfigured."""
    _require_admin(x_admin_token)
    result = await manager.revoke()
    db.audit("info", "ssh_revoke")
    return result


@router.get("/status")
async def status(x_admin_token: str = Header(default="")):
    _require_admin(x_admin_token)
    return manager.status()
