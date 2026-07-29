#!/usr/bin/env bash
#
# Show a full-screen logo on the Pi's HDMI output (the TV's secondary input),
# instead of a Linux login console. Paints a PNG straight to the framebuffer
# at boot via a tiny systemd oneshot service -- no desktop, no browser, costs
# ~nothing on a Pi Zero 2 W.
#
# The image is drawn once by `fbi`; the pixels then persist on the framebuffer
# because the console login (getty@tty1) is disabled so nothing overwrites it.
#
# Usage:
#   sudo ./setup-logo.sh /path/to/logo.png
#   sudo LOGO_SRC=/path/to/logo.png ./setup-logo.sh
#
# Best results: a PNG at the panel's native resolution (e.g. 1920x1080) with
# the artwork centered on a solid background -- letterboxing then blends in.
#
set -euo pipefail

LOGO_SRC="${1:-${LOGO_SRC:-}}"
LOGO_DIR="/opt/tv-logo"
LOGO_DST="$LOGO_DIR/logo.png"
UNIT="/etc/systemd/system/tv-logo.service"

if [ "$(id -u)" -ne 0 ]; then
  echo ">> Re-running with sudo..."
  exec sudo -E bash "$0" "$@"
fi

if [ -z "$LOGO_SRC" ] || [ ! -f "$LOGO_SRC" ]; then
  echo "!! Provide a logo image: sudo ./setup-logo.sh /path/to/logo.png"
  exit 1
fi

if [ ! -e /dev/fb0 ]; then
  echo "!! /dev/fb0 not found -- no framebuffer to draw on. Is HDMI connected?"
  exit 1
fi

echo ">> Installing fbi (framebuffer image viewer)..."
if ! command -v fbi >/dev/null 2>&1; then
  apt-get update -qq || true
  apt-get install -y fbi
fi

echo ">> Staging logo at $LOGO_DST"
mkdir -p "$LOGO_DIR"
install -m 0644 "$LOGO_SRC" "$LOGO_DST"

echo ">> Disabling console login on tty1 (so it can't overwrite the logo)"
# Local console login off; SSH is unaffected. Reversible:
#   sudo systemctl enable --now getty@tty1.service
systemctl disable --now getty@tty1.service 2>/dev/null || true

echo ">> Writing $UNIT"
cat > "$UNIT" <<EOF
[Unit]
Description=Canyon Hills logo on HDMI console
After=multi-user.target

[Service]
Type=oneshot
RemainAfterExit=yes
TimeoutStartSec=30
ExecStartPre=/usr/bin/chvt 1
ExecStart=/usr/bin/fbi -d /dev/fb0 -T 1 --noverbose -a --nocomments $LOGO_DST

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable tv-logo.service
systemctl restart tv-logo.service
sleep 1

echo
echo ">> Status:"
systemctl --no-pager --lines=0 status tv-logo.service | head -4 || true
echo
echo ">> Done. Switch the TV to the Pi's HDMI input to see the logo."
echo "   To update the image later: replace $LOGO_DST and 'sudo systemctl restart tv-logo'."
echo "   To undo entirely:"
echo "     sudo systemctl disable --now tv-logo.service && sudo rm $UNIT"
echo "     sudo systemctl enable --now getty@tty1.service   # restore console login"
