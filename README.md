# SSH Broker — v1.3.3

A single-connection SSH **broker / proxy**. Instead of every app holding its own
SSH credentials to your server, they all talk to one small API. The broker keeps
**one** authenticated SSH connection to the host and multiplexes every app's
request over it — capability-gated, rate-limited, and fully audit-logged.

```
                 ┌───────────────────────────────────────────────┐
   App A  ─┐     │  API container  (10.11.15.10:8000)             │
   App B  ─┼──►  │  ┌─────────────┐   one persistent SSH conn     │      ┌──────────┐
   App C  ─┘     │  │ plugin auth │──► multiplexed channels ──────┼────► │  Host    │
                 │  │ + rate limit│    + SFTP + metrics poller     │  ssh │  (target)│
   Admin ──────► │  └─────────────┘                                │      └──────────┘
     (browser)   │  Web container  (10.11.15.11:8080) ── login, plugins, logs
                 └───────────────────────────────────────────────┘
```

- **API** — `10.11.15.10:8000` — the "mean structure". Holds the SSH key, exposes the plugin surface.
- **Web** — `10.11.15.11:8080` — admin dashboard: sign-up/approval, plugins, host status, logs, and **Acquire SSH key**.
- **Host** — the server being managed. Its login is entered **once** through the UI to bootstrap key auth; nothing is stored in this repo.

New in v1.1: **no SSH key or server credential lives in the repo or on the
server beforehand.** The admin acquires the key at runtime via the web UI, and
new sign-ups require admin approval.

Everything runs in Docker on a macvlan network (`br1`) with static IPs. No
`docker-compose` required on the server — deployment is plain `docker run`.

---

## Table of contents
1. [Why a broker instead of per-app SSH](#1-why)
2. [Repository layout](#2-layout)
3. [Concepts: plugins & capabilities](#3-concepts)
4. [First-time deployment (unraid)](#4-deploy)
5. [Using it from another project](#5-use)
6. [The web dashboard](#6-web)
7. [API reference](#7-api)
8. [Updating / redeploying](#8-update)
9. [Security notes & limitations](#9-security)

---

## 1. Why a broker instead of per-app SSH <a name="1-why"></a>

| | Per-app SSH creds | This broker |
|---|---|---|
| Where the key lives | copied into every app | **one** place |
| If an app is compromised | full shell on the host | only that app's grant |
| Audit | scattered across apps | one central log |
| Per-app permissions | manage N keys on the host | capability grant per plugin |
| Rate limiting / backpressure | none | built in |

The trade-off: the broker is now the highest-value target, so it runs non-root,
holds the key as a read-only mount, is reachable only on your LAN/macvlan, and
authenticates every caller.

---

## 2. Repository layout <a name="2-layout"></a>

```
ssh-broker/
├── api/                     # the broker (FastAPI + asyncssh)
│   ├── app/
│   │   ├── main.py          # app + lifespan (starts SSH + metrics poller)
│   │   ├── ssh_manager.py   # single connection, reconnect, multiplex, SFTP, metrics
│   │   ├── auth.py          # api-key auth, capability checks, rate limit
│   │   ├── plugin_loader.py # load plugins from yaml / register at runtime
│   │   ├── db.py            # sqlite: users, plugins, logs
│   │   └── routers/         # metrics, exec, plugins, logs, system (acquire)
│   └── plugins/             # declarative plugin definitions (yaml)
├── web/                     # admin dashboard (FastAPI + Jinja)
├── docker/                  # api.Dockerfile, web.Dockerfile
├── deploy/                  # deploy.sh, broker.env.example
├── cli/broker-cli.py        # stdlib-only client for other projects
├── PLUGIN_SPEC.md           # how to write a plugin
└── README.md                # this file
```

---

## 3. Concepts: plugins & capabilities <a name="3-concepts"></a>

A **plugin** = one client app's contract. It declares which **metrics** it can
read, which named **commands** it can run (with per-parameter regex validation),
and whether it can **upload** files (restricted to a path prefix). Each plugin
gets its own API key (stored hashed) and its own rate limit and audit trail.

Full details and examples in **[PLUGIN_SPEC.md](PLUGIN_SPEC.md)**. Two example
plugins ship in `api/plugins/`: `host-metrics` (read-only) and
`cloud-code-docker` (pull images / manage containers).

---

## 4. First-time deployment (unraid) <a name="4-deploy"></a>

> No SSH key or server credential is needed on the server up front — the admin
> acquires it later through the web UI.

**Step 1 — get the code onto the server.** Clone it somewhere persistent:

```bash
mkdir -p /mnt/user/appdata && cd /mnt/user/appdata
git clone https://github.com/ryniouz/ssh-broker.git
cd ssh-broker
```

**Step 2 — configure.** Copy the env template and generate two secrets:

```bash
cp deploy/broker.env.example deploy/broker.env
echo "BROKER_ADMIN_TOKEN=$(openssl rand -hex 32)" >> deploy/broker.env
echo "WEB_SESSION_SECRET=$(openssl rand -hex 32)" >> deploy/broker.env
nano deploy/broker.env      # confirm IPs / NET_NAME / DATA_DIR
```

**Step 3 — create the macvlan network** (skip if `br1` already exists on unraid).
Assign IPs inside br1's real subnet (here `10.11.0.0/20`).

**Step 4 — build & launch both containers:**

```bash
./deploy/deploy.sh
```

You should see:

```
API : http://10.11.15.10:8000/health
Web : http://10.11.15.11:8080/
```

**Step 5 — create your admin account.** Open `http://10.11.15.11:8080/`. The
first sign-up becomes the **admin** (auto-approved). Later sign-ups are pending
until the admin approves them under **Users**.

**Step 6 — acquire the SSH key.** Go to **Settings → Acquire SSH key**, enter the
target host + username + password **once**. The broker installs its own generated
key on the host and discards the password. The dashboard then shows
**Broker → Host: CONNECTED**.

---

## 5. Using it from another project <a name="5-use"></a>

Any project can drive the broker with `cli/broker-cli.py` (pure stdlib, copy it
in) or plain HTTP.

**Register a plugin** (admin token — one-time, returns the API key):

```bash
cat > cloud-code.json <<'JSON'
{
  "name": "cloud-code",
  "description": "cloud code deploy agent",
  "capabilities": {
    "metrics": ["cpu","ram"],
    "commands": {
      "docker_pull": {"template":"docker pull $image","timeout":300,
        "params":{"image":{"pattern":"^[a-zA-Z0-9._/:@-]+$","required":true}}}
    },
    "upload": {"path_prefix":"/mnt/user/appdata/"}
  }
}
JSON

BROKER_ADMIN_TOKEN=<token> python cli/broker-cli.py \
  --api http://10.11.15.10:8000 register --file cloud-code.json
# -> { "status": "created", "api_key": "bpk_..." }   # save this key
```

**Then the app uses only its own API key:**

```bash
export BROKER_API_KEY=bpk_...
python cli/broker-cli.py metrics
python cli/broker-cli.py call docker_pull --param image=ghcr.io/me/app:latest
python cli/broker-cli.py upload ./compose.yml /mnt/user/appdata/app/compose.yml
```

**Or raw HTTP:**

```bash
curl -s http://10.11.15.10:8000/metrics -H "X-API-Key: bpk_..."
curl -s http://10.11.15.10:8000/exec/run -H "X-API-Key: bpk_..." \
  -H "Content-Type: application/json" \
  -d '{"command":"docker_pull","params":{"image":"ghcr.io/me/app:latest"}}'
```

---

## 6. The web dashboard <a name="6-web"></a>

`http://10.11.15.11:8080/`

- **Sign up / approval** — first sign-up is the admin; later sign-ups are
  **pending** until the admin approves them under **Users**.
- **Dashboard** — broker↔host status and every plugin (capabilities, rate limit,
  last-seen, enable/disable). Shows a banner when accounts await approval.
- **Users** (admin) — approve / deny / remove accounts.
- **Settings** (admin) — **Acquire SSH key**: enter the host login once to
  bootstrap key auth; the password is never stored.
- **Logs** — the full audit trail, filterable by level (`info`/`error`/`denied`)
  and plugin.
- **Manual** — this document, rendered in-app.

---

## 7. API reference <a name="7-api"></a>

Interactive docs: `http://10.11.15.10:8000/docs`

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET  | `/health` | none | broker + SSH status |
| GET  | `/metrics` | `X-API-Key` | cached host metrics (filtered by grant) |
| POST | `/exec/run` | `X-API-Key` | run a named, whitelisted command |
| POST | `/exec/upload` | `X-API-Key` | SFTP a file to an allowed path |
| GET  | `/plugins` | `X-Admin-Token` | list plugins (incl. last-seen + client IP) |
| GET  | `/plugins/{name}` | `X-Admin-Token` | one plugin's full config |
| POST | `/plugins` | `X-Admin-Token` | create/update a plugin |
| POST | `/plugins/{name}/enable` \| `/disable` | `X-Admin-Token` | toggle |
| POST | `/plugins/{name}/rotate-key` | `X-Admin-Token` | mint a new API key (shown once) |
| DELETE | `/plugins/{name}` | `X-Admin-Token` | delete a plugin |
| GET  | `/logs?plugin=<name>` | `X-Admin-Token` | audit log (filter per plugin) |
| POST | `/ssh/test` | `X-Admin-Token` | test a login (stores nothing) |
| POST | `/ssh/acquire` | `X-Admin-Token` | one-time key bootstrap (host/user/password) |
| POST | `/ssh/revoke` | `X-Admin-Token` | delete stored key + target (disconnect) |
| GET  | `/ssh/status` | `X-Admin-Token` | SSH target + connection status + auth method |

---

## 8. Updating / redeploying <a name="8-update"></a>

From your dev machine: commit and push. On the server, pull and re-run deploy —
it rebuilds the images and swaps the containers (data, users, keys survive in the
mounted volume):

```bash
ssh root@<host> 'cd /mnt/user/appdata/ssh-broker && git pull && ./deploy/deploy.sh'
```

---

## 9. Security notes & limitations <a name="9-security"></a>

- **The broker holds the only key.** Guard the `secrets/` mount and the admin
  token. Both are gitignored and must never be committed.
- **Single connection = single point of failure.** It auto-reconnects with
  backoff; for HA you'd add a small pool (future work).
- **Do not expose a generic "run any shell" command** to plugins — that hands
  back the very access this design removes. Keep commands named + validated.
- **Keep it on the LAN/macvlan.** Nothing here should face the public internet
  without a reverse proxy + TLS + stronger auth in front.
- **Metrics are cached** (polled every few seconds) — values can be a few
  seconds stale by design, which is what keeps host load low.
- The host password is only used once, at **Acquire SSH key** time, to install
  the broker's public key. It is never written to disk or logs. Rotate it
  afterwards if you like; the broker uses key auth from then on.
```
