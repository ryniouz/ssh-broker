# SSH Broker — Operator Manual (v1.1.0)

One persistent SSH connection to the host, shared by all your apps through a
small capability-gated API. Apps never hold SSH creds — they hold a scoped API
key. This page is the living copy of the manual; the same text ships in the
repo `README.md` and `PLUGIN_SPEC.md`.

## This deployment

| Component | Address | Notes |
|-----------|---------|-------|
| API (broker) | `http://10.11.15.10:8000` | holds the SSH key, serves plugins |
| Web (this UI) | `http://10.11.15.11:8080` | admin dashboard |
| Managed host | acquired at runtime | e.g. `10.10.2.3` (beta-vault, unraid) |
| Network | `br1` macvlan (`10.11.0.0/20`) | containers have static IPs |
| Data dir | `/mnt/user/appdata/ssh-broker-data` | sqlite, keys, acquired SSH key |
| Source | `/mnt/user/appdata/ssh-broker` | git clone of the repo |

## First-run setup

1. Open the web UI. The **first sign-up becomes the admin** (auto-approved).
2. Go to **Settings → Acquire SSH key**. Enter the target host, username and
   password **once**. The broker generates its own keypair, installs the public
   key on the host, stores the private key, and **discards the password** (never
   saved). From then on it connects with key auth only.
3. The dashboard should now show **Broker → Host: CONNECTED**.

## Accounts & approval

- The first account is the admin.
- Every later sign-up is created as **pending** and cannot log in until the
  admin approves it under **Users** (Approve / Deny).
- Admin-only tabs: **Users** and **Settings**.

## How it works

1. The API container opens **one** SSH connection to the host and keeps it warm
   (keepalives + auto-reconnect with backoff).
2. Each request from an app becomes a new multiplexed channel — no new handshake
   per call.
3. Host metrics (cpu/ram/disk) are polled on a timer and cached.
4. Every call is authenticated, capability-checked, rate-limited, and audit-logged.

## Add a plugin (onboard an app)

Register with the admin token; you get an API key back **once**.

```bash
cat > myapp.json <<'JSON'
{
  "name": "myapp",
  "description": "what this app does",
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

python cli/broker-cli.py --api http://10.11.15.10:8000 \
  --admin-token <ADMIN_TOKEN> register --file myapp.json
```

Command templates use `$param` placeholders (Docker's `{{.X}}` and awk `$8` pass
through). Every value is regex-validated then shell-quoted.

## Use the broker (as an app)

```bash
export BROKER_API_KEY=bpk_...
python cli/broker-cli.py --api http://10.11.15.10:8000 metrics
python cli/broker-cli.py --api http://10.11.15.10:8000 call docker_pull --param image=ghcr.io/me/app:latest
```

## Redeploy after a code change

```bash
git push                                   # your dev machine
ssh root@<host> 'cd /mnt/user/appdata/ssh-broker && git pull && ./deploy/deploy.sh'
```

Data, keys, users and the acquired SSH key live in the data dir and survive redeploys.

## Security notes

- The broker holds the **only** SSH key (non-root container, key in the data
  dir). A compromised app yields only that app's grant — not a shell on the host.
- The acquire password is used once and never stored on disk or in logs.
- Keep the admin token and `deploy/broker.env` secret; both are gitignored.
- Keep this on the LAN. Don't expose it to the internet without TLS + stronger
  auth in front.
