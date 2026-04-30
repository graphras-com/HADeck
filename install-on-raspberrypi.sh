#!/usr/bin/env bash
set -euo pipefail

# HADeck install script for Raspberry Pi (Debian/Raspbian)
# Run as root or with sudo.

INSTALL_DIR="/opt/hadeck"
SERVICE_USER="hadeck"

if [ "$(id -u)" -ne 0 ]; then
    echo "Error: This script must be run as root (use sudo)." >&2
    exit 1
fi

echo "==> Installing system dependencies..."
apt-get update
apt-get install -y libhidapi-libusb0 libcairo2-dev git

# Install uv if not present
if ! command -v uv &>/dev/null; then
    echo "==> Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Create service user if it doesn't exist
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "==> Creating service user '$SERVICE_USER'..."
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
else
    echo "==> Service user '$SERVICE_USER' already exists, skipping."
fi

# Set up udev rules for StreamDeck+ HID access
echo "==> Configuring udev rules..."
groupadd -f usbaccess
usermod -aG usbaccess "$SERVICE_USER"

cp deploy/99-hidapi.rules /etc/udev/rules.d/
udevadm control --reload-rules
udevadm trigger

# Install application
echo "==> Installing HADeck to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp -r . "$INSTALL_DIR/"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"

echo "==> Installing Python dependencies..."
cd "$INSTALL_DIR"
/root/.local/bin/uv sync

# Remind about .env
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo ""
    echo "WARNING: No .env file found at $INSTALL_DIR/.env"
    echo "Create it with HA_URL and HA_TOKEN before starting the service."
    echo ""
fi

# Install and enable systemd service
echo "==> Installing systemd service..."
cp deploy/hadeck.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable hadeck.service
echo "==> Service enabled. Start with: systemctl start hadeck.service"

echo "==> Installation complete."
