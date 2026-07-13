"""Single-connection SSH broker core.

Holds ONE authenticated SSH connection to the target host and multiplexes all
work over it. The target and key are acquired at runtime (see `acquire`): an
admin supplies host/user/password once through the web UI; the broker generates
its own keypair, installs the public key on the target, stores the private key,
and discards the password. The connection then uses key auth forever.

Also: auto-reconnect with backoff, concurrency cap, SFTP upload, and a cached
metrics poller so app requests don't each trigger a fresh round trip.
"""
import os
import json
import asyncio
import time
import shlex
import logging
from string import Template
from typing import Optional

import asyncssh

from .config import settings

log = logging.getLogger("broker.ssh")


class NotConfigured(Exception):
    """Raised when an operation is attempted before a key has been acquired."""


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
        self._target: Optional[dict] = None  # {host, port, user}
        self._load_target()

    # ---- target persistence ---------------------------------------------
    def _load_target(self) -> None:
        try:
            with open(settings.target_path, "r", encoding="utf-8") as f:
                self._target = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._target = None

    def _save_target(self) -> None:
        with open(settings.target_path, "w", encoding="utf-8") as f:
            json.dump(self._target, f)

    def is_configured(self) -> bool:
        return bool(self._target) and os.path.exists(settings.ssh_key_path)

    # ---- connection lifecycle -------------------------------------------
    async def _connect(self) -> asyncssh.SSHClientConnection:
        t = self._target
        log.info("Opening SSH connection to %s@%s:%s", t["user"], t["host"], t["port"])
        conn = await asyncssh.connect(
            host=t["host"],
            port=t["port"],
            username=t["user"],
            client_keys=[settings.ssh_key_path],
            known_hosts=None if not settings.ssh_known_hosts else settings.ssh_known_hosts,
            keepalive_interval=settings.ssh_keepalive,
            keepalive_count_max=3,
        )
        self._connected_since = time.time()
        self._last_error = None
        return conn

    async def connection(self) -> asyncssh.SSHClientConnection:
        if not self.is_configured():
            raise NotConfigured("no SSH target/key configured — acquire one first")
        if self._conn is not None and not self._conn.is_closed():
            return self._conn
        async with self._lock:
            if self._conn is not None and not self._conn.is_closed():
                return self._conn
            backoff = 1
            for _ in range(6):  # bounded here; the poll loop retries longer-term
                try:
                    self._conn = await self._connect()
                    return self._conn
                except Exception as e:  # noqa: BLE001
                    self._last_error = str(e)
                    log.warning("SSH connect failed: %s (retry in %ss)", e, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30)
            raise NotConfigured(f"could not establish SSH connection: {self._last_error}")

    async def _close_conn(self) -> None:
        if self._conn is not None and not self._conn.is_closed():
            self._conn.close()
            await self._conn.wait_closed()
        self._conn = None

    async def close(self) -> None:
        self._stop = True
        await self._close_conn()

    # ---- one-time key acquisition ---------------------------------------
    async def acquire(self, host: str, user: str, password: str, port: int = 22) -> dict:
        """Bootstrap key auth using a one-time password. The password is used to
        install the broker's public key on the target and is never stored."""
        # 1. generate a fresh keypair for the broker
        key = asyncssh.generate_private_key("ssh-ed25519", comment="ssh-broker")
        priv = key.export_private_key("openssh")           # bytes
        pub_line = key.export_public_key("openssh").decode().strip()

        # 2. connect with the password and install the public key
        install = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys "
            "&& chmod 600 ~/.ssh/authorized_keys "
            f"&& grep -qF {shlex.quote(pub_line)} ~/.ssh/authorized_keys "
            f"|| echo {shlex.quote(pub_line)} >> ~/.ssh/authorized_keys"
        )
        async with asyncssh.connect(
            host=host, port=port, username=user, password=password,
            known_hosts=None,
        ) as conn:
            res = await conn.run(install, check=False)
            if res.exit_status != 0:
                raise RuntimeError(f"failed to install key: {(res.stderr or '').strip()}")

        # 3. persist the private key (writable data dir) + the target
        with open(settings.ssh_key_path, "wb") as f:
            f.write(priv)
        os.chmod(settings.ssh_key_path, 0o600)
        self._target = {"host": host, "port": port, "user": user}
        self._save_target()

        # 4. drop any old connection and verify key auth works now
        await self._close_conn()
        verify = await self.run("echo OK", timeout=15)
        if verify["rc"] != 0:
            raise RuntimeError(f"key installed but verification failed: {verify['stderr']}")
        log.info("SSH key acquired for %s@%s", user, host)
        return {"ok": True, "host": host, "user": user, "verify": verify["stdout"]}

    # ---- operations ------------------------------------------------------
    async def run(self, command: str, timeout: int = 30) -> dict:
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
        """Render a `$param` template (string.Template) with shell-quoted params,
        then run it. This does NOT collide with Docker's `{{.X}}` or awk's `$8`.
        A missing placeholder is left literal (safe_substitute)."""
        safe = {k: shlex.quote(str(v)) for k, v in params.items()}
        command = Template(template).safe_substitute(safe)
        return await self.run(command, timeout=timeout)

    async def upload(self, local_bytes: bytes, remote_path: str) -> dict:
        async with self._sem:
            conn = await self.connection()
            async with conn.start_sftp_client() as sftp:
                async with sftp.open(remote_path, "wb") as f:
                    await f.write(local_bytes)
            return {"remote_path": remote_path, "bytes": len(local_bytes)}

    # ---- background metrics poller --------------------------------------
    async def poll_loop(self) -> None:
        while not self._stop:
            if self.is_configured():
                try:
                    self._metrics = await self._collect_metrics()
                    self._metrics_ts = time.time()
                except Exception as e:  # noqa: BLE001
                    self._last_error = str(e)
                    log.debug("metrics poll failed: %s", e)
            await asyncio.sleep(settings.metrics_poll_interval)

    async def _collect_metrics(self) -> dict:
        cpu = await self.run("top -bn1 | awk '/Cpu\\(s\\)/{print 100 - $8}'", timeout=15)
        mem = await self.run("free -m | awk '/Mem:/{printf \"%d %d %d\", $2,$3,$7}'", timeout=15)
        disk = await self.run("df -PB1 / | awk 'NR==2{printf \"%d %d %d\", $2,$3,$4}'", timeout=15)
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
        return {
            "data": self._metrics,
            "as_of": self._metrics_ts,
            "stale_seconds": round(time.time() - self._metrics_ts, 1) if self._metrics_ts else None,
        }

    def status(self) -> dict:
        return {
            "configured": self.is_configured(),
            "connected": self._conn is not None and not self._conn.is_closed(),
            "connected_since": self._connected_since,
            "last_error": self._last_error,
            "target": f"{self._target['user']}@{self._target['host']}:{self._target['port']}" if self._target else None,
        }


manager = SSHManager()
