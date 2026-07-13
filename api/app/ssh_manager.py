"""Single-connection SSH broker core.

Holds ONE authenticated SSH connection to the target host and multiplexes all
work over it as separate channels. Automatically reconnects with backoff, caps
concurrency with a semaphore, exposes command execution + SFTP upload, and runs
a background poller that caches host metrics so app requests don't each trigger
a fresh round trip.
"""
import asyncio
import time
import shlex
import logging
from string import Template
from typing import Optional

import asyncssh

from .config import settings

log = logging.getLogger("broker.ssh")


class SSHManager:
    def __init__(self) -> None:
        self._conn: Optional[asyncssh.SSHClientConnection] = None
        self._lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(settings.ssh_max_channels)
        self._connected_since: Optional[float] = None
        self._last_error: Optional[str] = None
        self._metrics: dict = {}
        self._metrics_ts: float = 0.0
        self._stop = False

    # ---- connection lifecycle -------------------------------------------
    async def _connect(self) -> asyncssh.SSHClientConnection:
        log.info("Opening SSH connection to %s@%s:%s", settings.ssh_user, settings.ssh_host, settings.ssh_port)
        conn = await asyncssh.connect(
            host=settings.ssh_host,
            port=settings.ssh_port,
            username=settings.ssh_user,
            client_keys=[settings.ssh_key_path],
            known_hosts=None if not settings.ssh_known_hosts else settings.ssh_known_hosts,
            keepalive_interval=settings.ssh_keepalive,
            keepalive_count_max=3,
        )
        self._connected_since = time.time()
        self._last_error = None
        return conn

    async def connection(self) -> asyncssh.SSHClientConnection:
        """Return a live connection, (re)connecting under a lock if needed."""
        if self._conn is not None and not self._conn.is_closed():
            return self._conn
        async with self._lock:
            if self._conn is not None and not self._conn.is_closed():
                return self._conn
            backoff = 1
            while True:
                try:
                    self._conn = await self._connect()
                    return self._conn
                except Exception as e:  # noqa: BLE001
                    self._last_error = str(e)
                    log.warning("SSH connect failed: %s (retry in %ss)", e, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)

    async def close(self) -> None:
        self._stop = True
        if self._conn is not None and not self._conn.is_closed():
            self._conn.close()
            await self._conn.wait_closed()

    # ---- operations ------------------------------------------------------
    async def run(self, command: str, timeout: int = 30) -> dict:
        """Run a shell command over a fresh channel. Returns stdout/stderr/rc."""
        async with self._sem:
            conn = await self.connection()
            try:
                res = await asyncio.wait_for(conn.run(command, check=False), timeout=timeout)
            except asyncio.TimeoutError:
                return {"rc": 124, "stdout": "", "stderr": f"timeout after {timeout}s", "command": command}
            return {
                "rc": res.exit_status,
                "stdout": (res.stdout or "").strip(),
                "stderr": (res.stderr or "").strip(),
                "command": command,
            }

    async def run_template(self, template: str, params: dict[str, str], timeout: int = 60) -> dict:
        """Render a command template with shell-quoted params, then run it.

        Templates use `$param` / `${param}` placeholders (string.Template). This
        deliberately does NOT collide with Docker's Go-template `{{.X}}` syntax or
        awk's `$8`, so those pass through untouched. Every supplied value is
        shlex.quote'd so a plugin cannot smuggle shell metacharacters through a
        parameter; values should also be regex-validated by the caller first.
        A referenced-but-missing placeholder is left literal (safe_substitute).
        """
        safe = {k: shlex.quote(str(v)) for k, v in params.items()}
        command = Template(template).safe_substitute(safe)
        return await self.run(command, timeout=timeout)

    async def upload(self, local_bytes: bytes, remote_path: str) -> dict:
        """Push a file to the host over the SFTP subsystem of the same conn."""
        async with self._sem:
            conn = await self.connection()
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(remote_path, "wb") as f:
                    await f.write(local_bytes)
            return {"remote_path": remote_path, "bytes": len(local_bytes)}

    # ---- background metrics poller --------------------------------------
    async def poll_loop(self) -> None:
        """Refresh cached host metrics on an interval; serve the cache to apps."""
        while not self._stop:
            try:
                self._metrics = await self._collect_metrics()
                self._metrics_ts = time.time()
            except Exception as e:  # noqa: BLE001
                self._last_error = str(e)
                log.debug("metrics poll failed: %s", e)
            await asyncio.sleep(settings.metrics_poll_interval)

    async def _collect_metrics(self) -> dict:
        # CPU %: 100 - idle, from top's %Cpu line. RAM/disk from free/df.
        cpu = await self.run(
            "top -bn1 | awk '/Cpu\\(s\\)/{print 100 - $8}'", timeout=15
        )
        mem = await self.run(
            "free -m | awk '/Mem:/{printf \"%d %d %d\", $2,$3,$7}'", timeout=15
        )
        disk = await self.run(
            "df -PB1 / | awk 'NR==2{printf \"%d %d %d\", $2,$3,$4}'", timeout=15
        )
        load = await self.run("cat /proc/loadavg | awk '{print $1, $2, $3}'", timeout=15)

        out: dict = {}
        try:
            out["cpu_percent"] = round(float(cpu["stdout"] or 0), 1)
        except ValueError:
            out["cpu_percent"] = None
        try:
            total, used, avail = (int(x) for x in mem["stdout"].split())
            out["mem_total_mb"], out["mem_used_mb"], out["mem_available_mb"] = total, used, avail
        except ValueError:
            pass
        try:
            total, used, avail = (int(x) for x in disk["stdout"].split())
            out["disk_total_b"], out["disk_used_b"], out["disk_avail_b"] = total, used, avail
        except ValueError:
            pass
        try:
            l1, l5, l15 = (float(x) for x in load["stdout"].split())
            out["load_1"], out["load_5"], out["load_15"] = l1, l5, l15
        except ValueError:
            pass
        return out

    def metrics(self) -> dict:
        return {"data": self._metrics, "as_of": self._metrics_ts, "stale_seconds": round(time.time() - self._metrics_ts, 1) if self._metrics_ts else None}

    def status(self) -> dict:
        return {
            "connected": self._conn is not None and not self._conn.is_closed(),
            "connected_since": self._connected_since,
            "last_error": self._last_error,
            "target": f"{settings.ssh_user}@{settings.ssh_host}:{settings.ssh_port}",
        }


manager = SSHManager()
