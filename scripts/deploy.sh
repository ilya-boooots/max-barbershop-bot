#!/usr/bin/env bash
set -euo pipefail

APP_NAME="max-barbershop-bot"
APP_DIR="/opt/bots/${APP_NAME}"
SERVICE_NAME="${APP_NAME}.service"
REPO_URL="${DEPLOY_REPO_URL:-}"
REF="${DEPLOY_REF:-origin/main}"

if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

if [ -z "${REPO_URL}" ] && [ ! -d "${APP_DIR}/.git" ]; then
  echo "DEPLOY_REPO_URL is required for the first clone into ${APP_DIR}" >&2
  exit 1
fi

${SUDO} mkdir -p "${APP_DIR}" "${APP_DIR}/data"

if [ -d "${APP_DIR}/.git" ]; then
  echo "Updating existing repository in ${APP_DIR}"
  if [ -n "${REPO_URL}" ]; then
    git -C "${APP_DIR}" fetch --prune "${REPO_URL}" main
    git -C "${APP_DIR}" reset --hard FETCH_HEAD
  else
    git -C "${APP_DIR}" fetch --prune origin main
    git -C "${APP_DIR}" reset --hard "${REF}"
  fi
else
  echo "Cloning repository into ${APP_DIR}"
  tmp_dir="$(mktemp -d)"
  git clone "${REPO_URL}" "${tmp_dir}"
  rsync -a --exclude .env --exclude data/ "${tmp_dir}/" "${APP_DIR}/"
  rm -rf "${tmp_dir}"
fi

${SUDO} mkdir -p "${APP_DIR}/data"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
if [ -f "${APP_DIR}/requirements.txt" ]; then
  "${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
fi

${SUDO} install -m 0644 "${APP_DIR}/scripts/systemd/${SERVICE_NAME}" "/etc/systemd/system/${SERVICE_NAME}"
${SUDO} systemctl daemon-reload
${SUDO} systemctl enable "${SERVICE_NAME}"
${SUDO} systemctl restart "${SERVICE_NAME}"
${SUDO} systemctl status "${SERVICE_NAME}" --no-pager
${SUDO} journalctl -u "${SERVICE_NAME}" -n 100 --no-pager
