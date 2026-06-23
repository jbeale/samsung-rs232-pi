#!/usr/bin/env bash
#
# Arm the Raspberry Pi hardware watchdog so the board auto-reboots if it ever
# hangs hard (kernel locked up, not even SSH-able). systemd pets /dev/watchdog;
# if it can't for ~14s, the SoC resets the Pi. Also reboots on kernel panic.
#
# The Broadcom (bcm2835) watchdog max timeout is ~15s -- it CANNOT be set
# longer. /dev/watchdog exists by default on Raspberry Pi OS (no config.txt
# change needed). Settings are written as drop-ins so OS updates don't clobber
# them.
#
# Usage:  sudo ./setup-watchdog.sh
#
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo ">> Re-running with sudo..."
  exec sudo bash "$0" "$@"
fi

if [ ! -e /dev/watchdog ]; then
  echo "!! /dev/watchdog not found. On a Pi this is unusual; on non-Pi hardware"
  echo "   you may need a watchdog driver / 'dtparam=watchdog=on' in config.txt."
  exit 1
fi

echo ">> Enabling hardware watchdog (14s) + kernel-panic reboot"

mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=14
RebootWatchdogSec=2min
EOF

cat > /etc/sysctl.d/99-panic-reboot.conf <<'EOF'
kernel.panic=10
kernel.panic_on_oops=1
EOF
/usr/sbin/sysctl -q -p /etc/sysctl.d/99-panic-reboot.conf

# Apply watchdog setting (re-execs PID 1; does NOT drop your SSH session).
systemctl daemon-reexec
sleep 1

echo
echo ">> Watchdog armed:"
systemctl show -p RuntimeWatchdogUSec -p RebootWatchdogUSec
/usr/sbin/sysctl kernel.panic kernel.panic_on_oops
echo
echo ">> Done. The Pi will now self-recover from hard hangs and kernel panics."
echo "   (Optional live test, reboots the Pi:  echo c | sudo tee /proc/sysrq-trigger )"
