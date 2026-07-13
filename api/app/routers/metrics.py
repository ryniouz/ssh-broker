"""Live host metrics, served from the broker's cached poll (no per-call SSH)."""
from fastapi import APIRouter, Depends, HTTPException

from .. import auth, db
from ..ssh_manager import manager

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("")
async def metrics(plugin: dict = Depends(auth.require_plugin)):
    snap = manager.metrics()
    allowed = plugin["capabilities"].get("metrics", [])
    # filter the cached snapshot to only the metric families this plugin may read
    data = snap["data"]
    filtered: dict = {}
    fam = {
        "cpu": ["cpu_percent", "load_1", "load_5", "load_15"],
        "ram": ["mem_total_mb", "mem_used_mb", "mem_available_mb"],
        "disk": ["disk_total_b", "disk_used_b", "disk_avail_b"],
    }
    for family in allowed:
        for k in fam.get(family, []):
            if k in data:
                filtered[k] = data[k]
    if not allowed:
        raise HTTPException(403, "no metric capabilities granted")
    db.audit("info", "metrics", plugin=plugin["name"])
    return {"as_of": snap["as_of"], "stale_seconds": snap["stale_seconds"], "data": filtered}
