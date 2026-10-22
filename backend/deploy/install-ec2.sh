#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo." >&2
  exit 1
fi

if [[ "$#" -ne 2 ]]; then
  echo "Usage: $0 <api-domain> <amplify-frontend-origin>" >&2
  exit 2
fi

API_DOMAIN="$1"
FRONTEND_ORIGIN="${2%/}"
PROJECT_DIR="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
BACKEND_DIR="${PROJECT_DIR}/backend"
MODEL_DIR="/var/lib/jrf-task-llm/models"
MODEL_PATH="${MODEL_DIR}/qwen2.5-1.5b-instruct-q4_k_m.gguf"
SERVICE_USER="jrf-llm"

if [[ ! -f "${BACKEND_DIR}/requirements.txt" ]]; then
  echo "Run this script from a cloned jrf-task-llm repository." >&2
  exit 1
fi

if [[ ! "${API_DOMAIN}" =~ ^[A-Za-z0-9.-]+$ || ! "${FRONTEND_ORIGIN}" =~ ^https://[A-Za-z0-9.-]+$ ]]; then
  echo "Provide an API DNS name and an HTTPS Amplify origin." >&2
  exit 2
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  debian-archive-keyring \
  debian-keyring \
  git \
  gnupg \
  python3.12-venv

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/jrf-task-llm --create-home \
    --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 \
  /var/lib/jrf-task-llm \
  "${MODEL_DIR}"

python3.12 -m venv "${BACKEND_DIR}/.venv"
"${BACKEND_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${BACKEND_DIR}/.venv/bin/python" -m pip install \
  -r "${BACKEND_DIR}/requirements.txt" \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

runuser -u "${SERVICE_USER}" -- \
  "${BACKEND_DIR}/.venv/bin/hf" download \
  Qwen/Qwen2.5-1.5B-Instruct-GGUF \
  qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --local-dir "${MODEL_DIR}"

cat >/etc/jrf-task-llm.env <<EOF
LOCAL_CHAT_MODEL_PATH=${MODEL_PATH}
OCR_API_BASE_URL=https://o0dqnexpsg.execute-api.ap-southeast-2.amazonaws.com/prod
JRF_TASK_LLM_DATA_DIR=/var/lib/jrf-task-llm/data
JRF_TASK_LLM_HOST=127.0.0.1
JRF_TASK_LLM_PORT=8787
JRF_TASK_LLM_MODEL_THREADS=2
JRF_TASK_LLM_ALLOWED_ORIGINS=${FRONTEND_ORIGIN}
EOF
chown root:"${SERVICE_USER}" /etc/jrf-task-llm.env
chmod 0640 /etc/jrf-task-llm.env

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 \
  /var/lib/jrf-task-llm/data

cat >/etc/systemd/system/jrf-task-llm.service <<EOF
[Unit]
Description=JRF Task local Hugging Face Q&A API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${BACKEND_DIR}
EnvironmentFile=/etc/jrf-task-llm.env
ExecStart=${BACKEND_DIR}/.venv/bin/python -m jrf_task_llm.local_server
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
ReadWritePaths=/var/lib/jrf-task-llm
UMask=0027

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now jrf-task-llm

curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
  | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
  | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y caddy

cat >/etc/caddy/Caddyfile <<EOF
${API_DOMAIN} {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8787
}
EOF
systemctl enable caddy
systemctl reload-or-restart caddy

echo "Setup complete. Check: systemctl status jrf-task-llm caddy"
echo "Health URL: https://${API_DOMAIN}/api/health"
