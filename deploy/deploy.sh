#!/bin/bash
# Server-side deploy script. Runs ON the unraid host (which has no docker-compose).
# It (re)builds both images and (re)launches both containers on the macvlan network
# with their static IPs. Idempotent: safe to run on every `git pull`.
#
#   ssh root@HOST 'cd /boot/config/plugins/ssh-broker && git pull && ./deploy/deploy.sh'
#
# Configuration comes from deploy/broker.env (NOT committed — see broker.env.example).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

ENV_FILE="${ROOT}/deploy/broker.env"
[ -f "$ENV_FILE" ] || { echo "!! missing ${ENV_FILE} (copy broker.env.example and fill it in)"; exit 1; }
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${NET_NAME:=br1}"
: "${API_IP:=10.11.20.1}"
: "${WEB_IP:=10.11.20.2}"
: "${DATA_DIR:=/mnt/user/appdata/ssh-broker}"
: "${SSH_KEY_FILE:=${DATA_DIR}/secrets/broker_ssh_key}"

echo "==> data dir:   ${DATA_DIR}"
echo "==> network:    ${NET_NAME}  api=${API_IP} web=${WEB_IP}"
mkdir -p "${DATA_DIR}/api" "${DATA_DIR}/web" "${DATA_DIR}/secrets"

# Containers run non-root (uid 10001 = api, 10002 = web). Bind mounts keep host
# ownership, so make the data dirs writable by those UIDs or the apps can't
# create their sqlite files.
chown -R 10001:10001 "${DATA_DIR}/api"
chown -R 10002:10002 "${DATA_DIR}/web"

if [ ! -f "${SSH_KEY_FILE}" ]; then
  echo "!! SSH key not found at ${SSH_KEY_FILE}"
  echo "   Run deploy/first-time-setup.sh first to generate + install it."
  exit 1
fi
# the api user must be able to read its mounted private key
chown 10001:10001 "${SSH_KEY_FILE}"
chmod 600 "${SSH_KEY_FILE}"

echo "==> building images"
docker build -f docker/api.Dockerfile -t ssh-broker-api:1.0.0 .
docker build -f docker/web.Dockerfile -t ssh-broker-web:1.0.0 .

echo "==> (re)starting api container"
docker rm -f ssh-broker-api 2>/dev/null || true
docker run -d --name ssh-broker-api --restart unless-stopped \
  --network "${NET_NAME}" --ip "${API_IP}" \
  -e BROKER_SSH_HOST="${BROKER_SSH_HOST}" \
  -e BROKER_SSH_USER="${BROKER_SSH_USER}" \
  -e BROKER_SSH_KEY_PATH="/run/secrets/broker_ssh_key" \
  -e BROKER_ADMIN_TOKEN="${BROKER_ADMIN_TOKEN}" \
  -v "${DATA_DIR}/api:/data" \
  -v "${SSH_KEY_FILE}:/run/secrets/broker_ssh_key:ro" \
  ssh-broker-api:1.0.0

echo "==> (re)starting web container"
docker rm -f ssh-broker-web 2>/dev/null || true
docker run -d --name ssh-broker-web --restart unless-stopped \
  --network "${NET_NAME}" --ip "${WEB_IP}" \
  -e WEB_API_BASE="http://${API_IP}:8000" \
  -e WEB_ADMIN_TOKEN="${BROKER_ADMIN_TOKEN}" \
  -e WEB_SESSION_SECRET="${WEB_SESSION_SECRET}" \
  -e WEB_DB_PATH="/data/web.db" \
  -v "${DATA_DIR}/web:/data" \
  ssh-broker-web:1.0.0

echo "==> done."
echo "    API : http://${API_IP}:8000/health"
echo "    Web : http://${WEB_IP}:8080/"
docker ps --filter name=ssh-broker --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
