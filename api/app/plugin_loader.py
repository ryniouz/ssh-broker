"""Load plugin definitions from YAML files and sync them into the database.

A "plugin" is the contract for one client app: which metrics it can read and
which named, parameterised commands it may run. Plugins can be defined two ways
and both end up in the same table:

  1. Declaratively  - drop a `plugins/<name>/plugin.yaml` file (version controlled).
  2. At runtime      - POST /plugins from the CLI / another app.

On first sight of a file-defined plugin we mint an API key, store only its hash,
and write the one-time plaintext to `<data>/keys/<name>.key` for the admin.
"""
import os
import re
import json
import time
import glob
import logging

import yaml

from . import db, auth
from .config import settings

log = logging.getLogger("broker.plugins")


def _validate_capabilities(caps: dict) -> dict:
    caps = caps or {}
    metrics = caps.get("metrics", []) or []
    commands = caps.get("commands", {}) or {}
    # sanity check each command spec
    for name, spec in commands.items():
        if "template" not in spec:
            raise ValueError(f"command '{name}' missing template")
        for pname, pspec in (spec.get("params") or {}).items():
            # compile the regex now so a bad pattern fails loudly at load time
            re.compile(pspec.get("pattern", ".*"))
    return {"metrics": list(metrics), "commands": commands}


def upsert_plugin(name: str, description: str, capabilities: dict,
                  enabled: bool = True, rate_limit_per_min: int = 60) -> dict | None:
    """Create or update a plugin. Returns {'api_key': ...} only when newly created."""
    caps = _validate_capabilities(capabilities)
    rows = db.q("SELECT * FROM plugins WHERE name=?", (name,))
    if rows:
        db.execute(
            "UPDATE plugins SET description=?, capabilities=?, enabled=?, rate_limit_per_min=? WHERE name=?",
            (description, json.dumps(caps), int(enabled), rate_limit_per_min, name),
        )
        db.audit("info", "plugin_update", plugin=name)
        return None
    pid = "pl_" + os.urandom(6).hex()
    key, key_hash = auth.new_api_key()
    db.execute(
        "INSERT INTO plugins (id,name,description,api_key_hash,enabled,capabilities,rate_limit_per_min,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (pid, name, description, key_hash, int(enabled), json.dumps(caps), rate_limit_per_min, time.time()),
    )
    db.audit("info", "plugin_create", plugin=name)
    return {"id": pid, "api_key": key}


def sync_from_files() -> None:
    """Load every plugins/*/plugin.yaml into the DB on startup."""
    keys_dir = os.path.join(settings.data_dir, "keys")
    os.makedirs(keys_dir, exist_ok=True)
    for path in glob.glob(os.path.join(settings.plugins_dir, "*", "plugin.yaml")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                spec = yaml.safe_load(f) or {}
            name = spec["name"]
            created = upsert_plugin(
                name=name,
                description=spec.get("description", ""),
                capabilities=spec.get("capabilities", {}),
                enabled=spec.get("enabled", True),
                rate_limit_per_min=spec.get("rate_limit_per_min", 60),
            )
            if created:
                key_file = os.path.join(keys_dir, f"{name}.key")
                with open(key_file, "w", encoding="utf-8") as kf:
                    kf.write(created["api_key"] + "\n")
                os.chmod(key_file, 0o600)
                log.warning("Plugin '%s' registered. One-time API key written to %s", name, key_file)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to load plugin file %s: %s", path, e)
