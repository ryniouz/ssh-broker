"""Central configuration, loaded from environment variables.

Nothing secret is hardcoded here, and (as of v1.1) the SSH target is NOT
configured via env at all — an admin acquires it at runtime through the web UI
("Acquire SSH key"), which writes the key + target into the data dir. The env
vars below only tune behaviour.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROKER_", env_file=".env", extra="ignore")

    # --- SSH tuning (target host/user/key are set at runtime via acquire) ---
    ssh_port_default: int = 22
    # Where the broker's private key is stored once acquired. Must be writable.
    ssh_key_path: str = "/data/broker_ssh_key"
    # Known-hosts pinning. Empty (default) disables host-key verification, which
    # is acceptable for a LAN-only broker connecting to a known host.
    ssh_known_hosts: str = ""

    ssh_max_channels: int = 8
    ssh_keepalive: int = 15
    metrics_poll_interval: int = 5

    # --- storage ---
    data_dir: str = "/data"
    plugins_dir: str = "/app/plugins"

    # Shared secret that lets the CLI / web backend manage plugins + acquire the
    # SSH key over the API. Set via BROKER_ADMIN_TOKEN.
    admin_token: str = ""

    @property
    def db_path(self) -> str:
        return str(Path(self.data_dir) / "broker.db")

    @property
    def target_path(self) -> str:
        # persisted SSH target (host/port/user) — never contains a password
        return str(Path(self.data_dir) / "ssh_target.json")


settings = Settings()
