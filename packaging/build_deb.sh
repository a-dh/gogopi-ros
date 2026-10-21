#!/usr/bin/env bash
# Build gogopi-services_X.Y.Z_all.deb
# Run from any directory; requires dpkg-deb and SSH access to gratzi.
# Must run ON THE ROBOT: the bundled venv is tied to the build host's
# architecture and Python version (lib/python3.X), so an x86 build won't run on the Pi.
set -euo pipefail

if [ "$(uname -m)" != "aarch64" ]; then
    echo "build_deb.sh: refusing to build on $(uname -m) — run it on the robot (aarch64)." >&2
    echo "  e.g. ssh -A albertd@gopiros2.local 'cd ~/gopiros2 && git pull && ./packaging/build_deb.sh'" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(grep '^version' "$REPO_ROOT/pyproject.toml" | grep -oP '[\d.]+')"
PKG="gogopi-services_${VERSION}_all"
STAGING="$REPO_ROOT/packaging/staging"
OUT="$REPO_ROOT/packaging/${PKG}.deb"

rm -rf "$STAGING"
install -d \
    "$STAGING/opt/gogopi/services" \
    "$STAGING/lib/systemd/system" \
    "$STAGING/lib/systemd/system/rpi-led-proxy.service.d" \
    "$STAGING/DEBIAN"

# Service scripts
cp "$REPO_ROOT/services/hw_button_scan.py" "$STAGING/opt/gogopi/services/"
cp "$REPO_ROOT/services/hw_wifi.py"        "$STAGING/opt/gogopi/services/"

# Systemd units
cp "$REPO_ROOT/services/hw_button_scan.service" "$STAGING/lib/systemd/system/"
cp "$REPO_ROOT/services/hw_wifi.service"        "$STAGING/lib/systemd/system/"

# rpi-led-proxy drop-in: hat nav lights (starboard green / port red) from early boot
cp "$REPO_ROOT/packaging/rpi-led-proxy-nav-lights.conf" \
    "$STAGING/lib/systemd/system/rpi-led-proxy.service.d/gogopi-nav-lights.conf"

# Venv with runtime deps
python3 -m venv "$STAGING/opt/gogopi/venv"
"$STAGING/opt/gogopi/venv/bin/pip" install --quiet \
    pigpio \
    pywifi \
    smbus2 \
    "rpi-led-proxy @ git+ssh://git@gratzi:2222/albertd/rpi_ws281x_proxy.git"

# DEBIAN control files
sed "s/^Version: .*/Version: $VERSION/" "$REPO_ROOT/packaging/control" \
    > "$STAGING/DEBIAN/control"
cp "$REPO_ROOT/packaging/postinst" "$STAGING/DEBIAN/postinst"
cp "$REPO_ROOT/packaging/prerm"    "$STAGING/DEBIAN/prerm"
chmod 755 "$STAGING/DEBIAN/postinst" "$STAGING/DEBIAN/prerm"

dpkg-deb --build --root-owner-group "$STAGING" "$OUT"
echo "Built: $OUT"
