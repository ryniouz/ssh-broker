#!/bin/bash
# Server-side deploy script. Runs ON the unraid host (which has no docker-compose).
# It (re)builds both images and (re)launches both containers on the macvlan network
# with their static IPs. Idempotent: safe to run on every `git pull`.
#
#   ssh root@HOST 'cd /mnt/user/appdata/ssh-broker && git pull && ./deploy/deploy.sh'
#
# The broker starts WITHOUT an SSH key. An admin acquires it after first login
# via the web UI (Settings -> Acquire SSH key); the key is stored in the data dir.
# Configuration comes from deploy/broker.env (NOT committed — see broker.env.example).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

ENV_FILE="${ROOT}/deploy/broker.env"
[ -f "$ENV_FILE" ] || { echo "!! missing ${ENV_FILE} (copy broker.env.example and fill it in)"; exit 1; }
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

: "${NET_NAME:=br1}"
: "${API_IP:=10.11.15.10}"
: "${WEB_IP:=10.11.15.11}"
: "${DATA_DIR:=/mnt/user/appdata/ssh-broker-data}"
: "${IMAGE_TAG:=1.2.5}"

echo "==> data dir:   ${DATA_DIR}"
echo "==> network:    ${NET_NAME}  api=${API_IP} web=${WEB_IP}"
mkdir -p "${DATA_DIR}/api" "${DATA_DIR}/web"

# Containers run non-root (uid 10001 = api, 10002 = web). Bind mounts keep host
# ownership, so make the data dirs writable by those UIDs or the apps can't
# create their sqlite files / store the acquired key.
chown -R 10001:10001 "${DATA_DIR}/api"
chown -R 10002:10002 "${DATA_DIR}/web"

echo "==> building images"
docker build -f docker/api.Dockerfile -t "ssh-broker-api:${IMAGE_TAG}" .
docker build -f docker/web.Dockerfile -t "ssh-broker-web:${IMAGE_TAG}" .

echo "==> (re)starting api container"
docker rm -f ssh-broker-api 2>/dev/null || true
docker run -d --name ssh-broker-api --restart unless-stopped \
  --network "${NET_NAME}" --ip "${API_IP}" \
  -e BROKER_ADMIN_TOKEN="${BROKER_ADMIN_TOKEN}" \
  -v "${DATA_DIR}/api:/data" \
  "ssh-broker-api:${IMAGE_TAG}"

echo "==> (re)starting web container"
docker rm -f ssh-broker-web 2>/dev/null || true
docker run -d --name ssh-broker-web --restart unless-stopped \
  --network "${NET_NAME}" --ip "${WEB_IP}" \
  -e WEB_API_BASE="http://${API_IP}:8000" \
  -e WEB_ADMIN_TOKEN="${BROKER_ADMIN_TOKEN}" \
  -e WEB_SESSION_SECRET="${WEB_SESSION_SECRET}" \
  -e WEB_DB_PATH="/data/web.db" \
  -v "${DATA_DIR}/web:/data" \
  "ssh-broker-web:${IMAGE_TAG}"

echo "==> done."
echo "    API : http://${API_IP}:8000/health"
echo "    Web : http://${WEB_IP}:8080/  (sign up -> Settings -> Acquire SSH key)"
docker ps --filter name=ssh-broker --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
