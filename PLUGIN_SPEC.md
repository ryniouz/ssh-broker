# Plugin specification

A **plugin** is the contract between one client app and the broker. It declares
exactly what that app is allowed to do — which metric families it can read, which
named commands it can run (and with what parameters), and whether it can upload
files. An app authenticates with its own API key and can never exceed its grant.

There are two ways to define a plugin. Both land in the same `plugins` table.

---

## 1. Declarative (version-controlled) — `api/plugins/<name>/plugin.yaml`

```yaml
name: my-app                 # unique id, [a-z0-9-]
description: what this app does
enabled: true
rate_limit_per_min: 60       # requests/min for this key before 429

capabilities:
  # metric families this key may read: cpu | ram | disk
  metrics: [cpu, ram]

  # named, whitelisted commands. The app calls a command by NAME and passes
  # params. Params are regex-validated then shell-quoted before running.
  commands:
    restart_container:
      template: "docker restart $name"     # $param / ${param} placeholders
      timeout: 60                          # seconds
      params:
        name:
          pattern: "^[a-zA-Z0-9._-]+$"     # REQUIRED per param — reject anything else
          required: true

  # optional: allow SFTP uploads, restricted to a path prefix
  upload:
    path_prefix: "/mnt/user/appdata/"
```

On first startup the broker registers the plugin, mints an API key, stores only
its **sha256 hash**, and writes the one-time plaintext key to
`<DATA_DIR>/api/keys/<name>.key` (mode 600). Read it once, hand it to the app,
delete it.

Editing the YAML and restarting the API updates the grant **without** changing
the existing key.

---

## 2. Runtime (self-service) — `POST /plugins`  (admin token)

Send the same shape as JSON. Used by `broker-cli.py register` so another project
can onboard itself:

```json
{
  "name": "my-app",
  "description": "what this app does",
  "enabled": true,
  "rate_limit_per_min": 60,
  "capabilities": {
    "metrics": ["cpu", "ram"],
    "commands": {
      "restart_container": {
        "template": "docker restart $name",
        "timeout": 60,
        "params": { "name": { "pattern": "^[a-zA-Z0-9._-]+$", "required": true } }
      }
    },
    "upload": { "path_prefix": "/mnt/user/appdata/" }
  }
}
```

The response includes the API key **once**, at creation only.

---

## Security model (why this is safe)

| Guard | Effect |
|-------|--------|
| Named commands only | Apps never send raw shell. They pick from a whitelist. |
| Per-param regex | A param that doesn't match its pattern is rejected (400). |
| `$param` templating | Uses `string.Template`; can't collide with Docker `{{.X}}` / awk `$8`. |
| `shlex.quote` on every value | Shell metacharacters can't break out of the template. |
| Capability grant | An app can only touch metrics/commands/uploads it was granted. |
| Upload path prefix | Files can only be written under an allowed directory. |
| Per-key rate limit | A buggy/hostile app can't exhaust the single SSH connection. |
| Hashed keys + audit log | Keys are never stored in clear; every action is logged. |

The broker holds the **only** SSH key. Compromising one app yields only that
app's grant — not shell on the host.
