"""Command execution + file push, gated by each plugin's capability grant."""
import re
import base64

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db, auth
from ..ssh_manager import manager

router = APIRouter(prefix="/exec", tags=["exec"])


class RunRequest(BaseModel):
    command: str                      # the NAME of a whitelisted command, not raw shell
    params: dict[str, str] = {}


@router.post("/run")
async def run(req: RunRequest, plugin: dict = Depends(auth.require_plugin)):
    if not manager.is_configured():
        raise HTTPException(503, "broker SSH target not configured — an admin must acquire a key first")
    spec = auth.command_spec(plugin, req.command)
    if spec is None:
        db.audit("denied", "exec", plugin=plugin["name"], detail=f"command '{req.command}' not granted")
        raise HTTPException(403, f"command '{req.command}' not permitted for this plugin")

    # validate each supplied param against the plugin-declared regex
    declared = spec.get("params", {}) or {}
    for pname, pspec in declared.items():
        val = req.params.get(pname)
        if val is None:
            if pspec.get("required", True):
                raise HTTPException(400, f"missing param '{pname}'")
            continue
        pattern = pspec.get("pattern", ".*")
        if not re.fullmatch(pattern, str(val)):
            db.audit("denied", "exec", plugin=plugin["name"], detail=f"param '{pname}' failed pattern")
            raise HTTPException(400, f"param '{pname}' does not match required pattern")

    result = await manager.run_template(spec["template"], req.params, timeout=spec.get("timeout", 60))
    db.audit(
        "error" if result["rc"] != 0 else "info",
        "exec",
        plugin=plugin["name"],
        detail={"command": req.command, "rc": result["rc"]},
    )
    return result


class UploadRequest(BaseModel):
    remote_path: str
    content_base64: str


@router.post("/upload")
async def upload(req: UploadRequest, plugin: dict = Depends(auth.require_plugin)):
    # uploads require an explicit "upload" capability with an allowed path prefix
    up = plugin["capabilities"].get("upload")
    if not up:
        raise HTTPException(403, "upload not permitted for this plugin")
    if not manager.is_configured():
        raise HTTPException(503, "broker SSH target not configured — an admin must acquire a key first")
    allowed_prefix = up.get("path_prefix", "/tmp/")
    if not req.remote_path.startswith(allowed_prefix):
        db.audit("denied", "upload", plugin=plugin["name"], detail=req.remote_path)
        raise HTTPException(403, f"remote_path must start with {allowed_prefix}")
    data = base64.b64decode(req.content_base64)
    res = await manager.upload(data, req.remote_path)
    db.audit("info", "upload", plugin=plugin["name"], detail=res)
    return res
