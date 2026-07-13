"""SSH Broker API — the mean structure.

One persistent SSH connection to the target host, multiplexed and exposed to
client apps ("plugins") through a small, capability-gated REST surface.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db, __version__
from .config import settings
from .ssh_manager import manager
from .plugin_loader import sync_from_files
from .routers import exec as exec_router, metrics, plugins, logs, system

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("broker")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    sync_from_files()
    # warm the connection and kick off the metrics poller
    poll_task = asyncio.create_task(manager.poll_loop())
    log.info("Broker %s starting; target=%s", __version__, manager.status()["target"])
    try:
        yield
    finally:
        poll_task.cancel()
        await manager.close()


app = FastAPI(title="SSH Broker", version=__version__, lifespan=lifespan)
app.include_router(metrics.router)
app.include_router(exec_router.router)
app.include_router(plugins.router)
app.include_router(logs.router)
app.include_router(system.router)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": __version__, "ssh": manager.status()}
