"""Central configuration, loaded from environment variables.

Nothing secret is hardcoded here. The SSH target credentials and paths all
come from the environment (see .env.example). On the server these are passed
via `docker run --env-file` or individual `-e` flags.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROKER_", env_file=".env", extra="ignore")

    # --- SSH target (the "unraid" production box the broker manages) ---
    ssh_host: str = "10.10.2.3"
    ssh_port: int = 22
    ssh_user: str = "root"
    # Path to the private key INSIDE the container (mounted as a Docker secret/volume).
    ssh_key_path: str = "/run/secrets/broker_ssh_key"
    # Known-hosts pinning. Empty (default) disables host-key verification, which
    # is acceptable for a LAN-only broker connecting to a known host. To pin,
    # set BROKER_SSH_KNOWN_HOSTS to a path containing the host's public key.
    ssh_known_hosts: str = ""

    # Max concurrent SSH channels multiplexed over the single connection.
    ssh_max_channels: int = 8
    # Seconds between keepalive pings on the persistent connection.
    ssh_keepalive: int = 15
    # How often (seconds) the background poller refreshes cached host metrics.
    metrics_poll_interval: int = 5

    # --- storage ---
    data_dir: str = "/data"
    plugins_dir: str = "/app/plugins"

    # Shared secret that lets the CLI / web backend manage plugins over the API.
    # Set via BROKER_ADMIN_TOKEN. Empty means runtime plugin management is disabled.
    admin_token: str = ""

    @property
    def db_path(self) -> str:
        return str(Path(self.data_dir) / "broker.db")

    @property
    def log_path(self) -> str:
        return str(Path(self.data_dir) / "broker.log")


settings = Settings()
