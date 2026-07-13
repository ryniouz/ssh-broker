#!/bin/bash
# One-time bootstrap, run ON the unraid host. Generates a dedicated SSH key for
# the broker and authorises it so the broker container can open its single
# connection back to the host. After this, no passwords are used anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE="deploy/broker.env"
[ -f "$ENV_FILE" ] || { echo "!! missing ${ENV_FILE} (copy from broker.env.example)"; exit 1; }
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a
: "${DATA_DIR:=/mnt/user/appdata/ssh-broker}"
: "${SSH_KEY_FILE:=${DATA_DIR}/secrets/broker_ssh_key}"

mkdir -p "$(dirname "$SSH_KEY_FILE")"
if [ ! -f "$SSH_KEY_FILE" ]; then
  echo "==> generating ed25519 key for the broker"
  ssh-keygen -t ed25519 -N "" -C "ssh-broker" -f "$SSH_KEY_FILE"
  chmod 600 "$SSH_KEY_FILE"
fi

PUB="$(cat "${SSH_KEY_FILE}.pub")"
AUTH="/root/.ssh/authorized_keys"
mkdir -p /root/.ssh; chmod 700 /root/.ssh; touch "$AUTH"; chmod 600 "$AUTH"
if ! grep -qF "$PUB" "$AUTH"; then
  echo "$PUB" >> "$AUTH"
  echo "==> broker public key authorised for root"
else
  echo "==> broker public key already authorised"
fi

echo "==> verifying key-based login to ${BROKER_SSH_USER}@${BROKER_SSH_HOST}"
ssh -i "$SSH_KEY_FILE" -o StrictHostKeyChecking=accept-new -o BatchMode=yes \
    "${BROKER_SSH_USER}@${BROKER_SSH_HOST}" 'echo OK: $(hostname)'
echo "==> setup complete. You can now run deploy/deploy.sh"
