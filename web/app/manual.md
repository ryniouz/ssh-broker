# SSH Broker — Operator Manual (v1.0.0)

One persistent SSH connection to the host, shared by all your apps through a
small capability-gated API. Apps never hold SSH creds — they hold a scoped API
key. This page is the living copy of the manual; the same text ships in the
repo `README.md` and `PLUGIN_SPEC.md`.

## This deployment

| Component | Address | Notes |
|-----------|---------|-------|
| API (broker) | `http://10.11.15.10:8000` | holds the SSH key, serves plugins |
| Web (this UI) | `http://10.11.15.11:8080` | admin dashboard |
| Managed host | `10.10.2.3` (beta-vault) | unraid, reached over one SSH conn |
| Network | `br1` macvlan (`10.11.0.0/20`) | containers have static IPs |
| Data dir | `/mnt/user/appdata/ssh-broker-data` | sqlite, keys, secrets |
| Source | `/mnt/user/appdata/ssh-broker` | git clone of the repo |

Admin API operations use the **admin token** (`X-Admin-Token`), stored in
`deploy/broker.env` on the server. Plugin operations use that plugin's **API
key** (`X-API-Key`), issued once at registration.

## How it works

1. The API container opens **one** SSH connection to the host and keeps it warm
   (keepalives + auto-reconnect with backoff).
2. Each request from an app becomes a new multiplexed channel on that one
   connection — no new handshake per call.
3. Host metrics (cpu/ram/disk) are polled on a timer and cached, so app reads
   don't each hit the host.
4. Every call is authenticated, capability-checked, rate-limited, and written to
   the audit log you can see under **Logs**.

## Add a plugin (onboard an app)

A plugin declares exactly what one app may do. Register it with the admin token;
you get an API key back **once**.

```bash
# from any machine that can reach the API
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
# -> {"status":"created","api_key":"bpk_..."}
```

Command templates use `$param` placeholders (Docker's `{{.X}}` and awk `$8` pass
through untouched). Every value is regex-validated then shell-quoted, so an app
can never inject shell or exceed its grant.

## Use the broker (as an app)

```bash
export BROKER_API_KEY=bpk_...
python cli/broker-cli.py --api http://10.11.15.10:8000 metrics
python cli/broker-cli.py --api http://10.11.15.10:8000 call docker_pull --param image=ghcr.io/me/app:latest
```

Or raw HTTP:

```bash
curl -s http://10.11.15.10:8000/metrics -H "X-API-Key: bpk_..."
curl -s http://10.11.15.10:8000/exec/run -H "X-API-Key: bpk_..." \
  -H "Content-Type: application/json" \
  -d '{"command":"docker_pull","params":{"image":"ghcr.io/me/app:latest"}}'
```

## Redeploy after a code change

```bash
# your dev machine
git push

# the server (pulls via its read-only deploy key, rebuilds, swaps containers)
ssh root@10.10.2.3 'cd /mnt/user/appdata/ssh-broker && git pull && ./deploy/deploy.sh'
```

Data, keys and the admin token live in the mounted data dir and survive redeploys.

## Security notes

- The broker holds the **only** SSH key (read-only mount, non-root container). A
  compromised app yields only that app's grant — not a shell on the host.
- Keep the admin token and `deploy/broker.env` secret; both are gitignored.
- Keep this on the LAN. Don't expose it to the internet without TLS + stronger
  auth in front.
