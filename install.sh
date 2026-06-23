#!/usr/bin/env bash
#
# Install the Samsung RS-232 (Ex-Link) control service on a Raspberry Pi
# (or any systemd Linux). Idempotent -- safe to re-run to update.
#
# Usage:
#   sudo ./install.sh
#   sudo SERVICE_USER=admin SERIAL_PORT=/dev/ttyUSB0 ./install.sh
#
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/samsung-rs232}"
SERVICE_NAME="samsung-rs232"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$USER}}"
SERIAL_PORT="${SERIAL_PORT:-/dev/ttyUSB0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Re-exec with sudo if not root, preserving our config.
if [ "$(id -u)" -ne 0 ]; then
  echo ">> Re-running with sudo..."
  exec sudo -E env INSTALL_DIR="$INSTALL_DIR" SERVICE_USER="$SERVICE_USER" \
       SERIAL_PORT="$SERIAL_PORT" bash "$0" "$@"
fi

echo ">> Installing $SERVICE_NAME"
echo "   dir=$INSTALL_DIR  user=$SERVICE_USER  port=$SERIAL_PORT"

# --- prerequisites ---
command -v python3 >/dev/null 2>&1 || { echo "!! python3 is required"; exit 1; }
if ! python3 -m venv --help >/dev/null 2>&1; then
  echo ">> Installing python3-venv..."
  apt-get update -qq && apt-get install -y python3-venv
fi

# --- serial port access ---
usermod -aG dialout "$SERVICE_USER" || true

# --- app files ---
mkdir -p "$INSTALL_DIR"
install -m 0755 "$SCRIPT_DIR/exlink_server.py" "$INSTALL_DIR/exlink_server.py"

# --- python venv + deps ---
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
  echo ">> Creating virtualenv..."
  python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet flask pyserial
chown -R "$SERVICE_USER" "$INSTALL_DIR"

# --- systemd unit (patched for this host) ---
UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
sed -e "s|^User=.*|User=${SERVICE_USER}|" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${INSTALL_DIR}|" \
    -e "s|^Environment=EXLINK_PORT=.*|Environment=EXLINK_PORT=${SERIAL_PORT}|" \
    -e "s|^ExecStart=.*|ExecStart=${INSTALL_DIR}/venv/bin/python ${INSTALL_DIR}/exlink_server.py|" \
    "$SCRIPT_DIR/samsung-rs232.service" > "$UNIT"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
sleep 2

echo
echo ">> Status:"
systemctl --no-pager --lines=0 status "$SERVICE_NAME" | head -5 || true
echo
echo ">> Done. Test it:"
echo "     curl http://localhost:8080/"
echo "     curl http://localhost:8080/on"
echo
echo ">> Tip: also run ./setup-watchdog.sh to auto-reboot the Pi if it ever hangs."
