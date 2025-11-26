#!/usr/bin/env bash
set -euo pipefail

usage() {
cat <<USAGE
Usage: $0 --user <user> --manifest <url> [--device <device>]

Examples:
sudo $0 --user admin --manifest https://alive-radio.s3.eu-west-1.amazonaws.com/stream.json --device "alsa/plughw:CARD=Headphones,DEV=0"
USAGE
}

USER_NAME=""
MANIFEST_URL=""
DEVICE="alsa/plughw:CARD=Headphones,DEV=0"

while [[ $# -gt 0 ]]; do
case "$1" in
--user) USER_NAME="$2"; shift 2;;
--manifest) MANIFEST_URL="$2"; shift 2;;
--device) DEVICE="$2"; shift 2;;
-h|--help) usage; exit 0;;
*) echo "Unknown arg: $1"; usage; exit 1;;
esac
done

if [[ -z "$USER_NAME" || -z "$MANIFEST_URL" ]]; then
    echo "Error: --user and --manifest are required"
    usage
    exit 1
fi

#Packages

sudo apt-get update
sudo apt-get install -y python3 mpv ca-certificates alsa-utils hostapd dnsmasq iw wireless-tools rfkill

#Copy files

sudo install -m 0755 bootstream.py /usr/local/bin/bootstream.py
sudo install -m 0755 network-manager.py /usr/local/bin/network-manager.py
sudo install -m 0755 config-server.py /usr/local/bin/config-server.py
sudo install -m 0755 network-config.py /usr/local/bin/network-config.py

# Copy systemd services
sudo mkdir -p /etc/systemd/system
sudo install -m 0644 systemd/stream-player.service /etc/systemd/system/stream-player.service
sudo install -m 0644 systemd/network-manager.service /etc/systemd/system/network-manager.service
sudo install -m 0644 systemd/config-server.service /etc/systemd/system/config-server.service

# Copy hotspot configuration files (hostapd.conf is copied later, before configuring)
sudo mkdir -p /etc/dnsmasq.d
sudo install -m 0644 config/dnsmasq-hotspot.conf /etc/dnsmasq.d/hotspot.conf

# Copy web template
sudo mkdir -p /usr/local/share/bartix/templates
sudo install -m 0644 templates/config.html /usr/local/share/bartix/templates/config.html

# Configure hostapd to use our config
# First, ensure the config file exists (copy it before configuring)
sudo mkdir -p /etc/hostapd
sudo install -m 0644 config/hostapd.conf /etc/hostapd/hostapd.conf

# Unmask hostapd service (it might be masked by default)
sudo systemctl unmask hostapd 2>/dev/null || true

# Configure /etc/default/hostapd
if [ -f /etc/default/hostapd ]; then
    sudo sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
    sudo sed -i 's|^DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
    # Ensure DAEMON_CONF is set
    if ! grep -q "^DAEMON_CONF=" /etc/default/hostapd; then
        echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' | sudo tee -a /etc/default/hostapd
    fi
else
    # Create /etc/default/hostapd if it doesn't exist
    echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' | sudo tee /etc/default/hostapd
fi

# Disable dnsmasq from starting automatically (we'll control it)
sudo systemctl disable dnsmasq || true
sudo systemctl stop dnsmasq || true

# Disable wpa_supplicant from managing wlan0 (it conflicts with hostapd)
if [ -f /etc/dhcpcd.conf ]; then
    # Add denyinterfaces wlan0 to prevent dhcpcd from managing it
    if ! grep -q "denyinterfaces wlan0" /etc/dhcpcd.conf; then
        echo "denyinterfaces wlan0" | sudo tee -a /etc/dhcpcd.conf
    fi
fi

# Stop wpa_supplicant if running (will be started by network-manager when needed)
sudo systemctl stop wpa_supplicant || true

# Configure WiFi country code (required for proper AP mode operation)
echo "Configuring WiFi country code..."
# Try to detect country from system, default to NL (Netherlands)
COUNTRY_CODE="${WIFI_COUNTRY:-NL}"
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
    # Extract country code from existing config if present
    EXISTING_COUNTRY=$(grep -i "^country=" /etc/wpa_supplicant/wpa_supplicant.conf | cut -d= -f2 | tr -d ' ' || echo "")
    if [ -n "$EXISTING_COUNTRY" ]; then
        COUNTRY_CODE="$EXISTING_COUNTRY"
    fi
fi

# Set country code via iw (immediate effect)
sudo iw reg set "$COUNTRY_CODE" 2>/dev/null || true

# Set country code in wpa_supplicant.conf (persistent)
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
    if ! grep -q "^country=" /etc/wpa_supplicant/wpa_supplicant.conf; then
        # Add country code at the beginning
        sudo sed -i "1i country=$COUNTRY_CODE" /etc/wpa_supplicant/wpa_supplicant.conf
    else
        sudo sed -i "s/^country=.*/country=$COUNTRY_CODE/i" /etc/wpa_supplicant/wpa_supplicant.conf
    fi
else
    # Create wpa_supplicant.conf with country code
    sudo mkdir -p /etc/wpa_supplicant
    echo "country=$COUNTRY_CODE" | sudo tee /etc/wpa_supplicant/wpa_supplicant.conf > /dev/null
fi

# Configure firewall to allow config server (if ufw is installed)
if command -v ufw >/dev/null 2>&1; then
    echo "Configuring firewall..."
    sudo ufw allow 8080/tcp comment "Bartix config server" 2>/dev/null || true
fi

# Ensure network-config.py is executable and has correct permissions
sudo chmod +x /usr/local/bin/network-config.py
sudo chmod +x /usr/local/bin/config-server.py
sudo chmod +x /usr/local/bin/network-manager.py
sudo chmod +x /usr/local/bin/bootstream.py

# Verify all required files exist
echo "Verifying installation..."
MISSING_FILES=0
for file in /usr/local/bin/bootstream.py /usr/local/bin/network-manager.py /usr/local/bin/config-server.py /usr/local/bin/network-config.py \
            /etc/hostapd/hostapd.conf /etc/dnsmasq.d/hotspot.conf /usr/local/share/bartix/templates/config.html; do
    if [ ! -f "$file" ]; then
        echo "ERROR: Missing file: $file"
        MISSING_FILES=$((MISSING_FILES + 1))
    fi
done

if [ $MISSING_FILES -gt 0 ]; then
    echo "ERROR: $MISSING_FILES required file(s) are missing. Installation incomplete."
    exit 1
fi

#Replace service placeholders

# Replace service placeholders with actual values
sudo sed -i "s#^User=.*#User=${USER_NAME}#" /etc/systemd/system/stream-player.service
sudo sed -i "s#^Environment=STREAM_MANIFEST_URL=.*#Environment=STREAM_MANIFEST_URL=${MANIFEST_URL}#" /etc/systemd/system/stream-player.service
sudo sed -i "s#^Environment=MPV_AUDIO_DEVICE=.*#Environment=MPV_AUDIO_DEVICE=${DEVICE}#" /etc/systemd/system/stream-player.service

# Ensure audio access
sudo usermod -aG audio "$USER_NAME" || true

# Enable + start services

sudo systemctl daemon-reload

# Enable services (but don't start yet - let them start on boot)
sudo systemctl enable network-manager.service
sudo systemctl enable config-server.service
sudo systemctl enable stream-player.service

# Start services with proper ordering
echo "Starting services..."
sudo systemctl start network-manager.service
sleep 3  # Give network-manager time to initialize

# Check if network-manager started successfully
if ! systemctl is-active --quiet network-manager.service; then
    echo "Warning: network-manager.service failed to start. Check logs:"
    echo "  sudo journalctl -u network-manager.service -n 50"
fi

sudo systemctl start config-server.service
sleep 2

# Check if config-server started successfully
if ! systemctl is-active --quiet config-server.service; then
    echo "Warning: config-server.service failed to start. Check logs:"
    echo "  sudo journalctl -u config-server.service -n 50"
fi

sudo systemctl start stream-player.service
sleep 2

# Check service status
echo ""
echo "Service status:"
systemctl is-active network-manager.service >/dev/null && echo "  ✓ network-manager.service: active" || echo "  ✗ network-manager.service: failed"
systemctl is-active config-server.service >/dev/null && echo "  ✓ config-server.service: active" || echo "  ✗ config-server.service: failed"
systemctl is-active stream-player.service >/dev/null && echo "  ✓ stream-player.service: active" || echo "  ✗ stream-player.service: failed"

echo ""
echo "Installed successfully!"
echo ""
echo "Services:"
echo "  - network-manager.service: Manages network and WiFi hotspot"
echo "  - config-server.service: Web configuration interface"
echo "  - stream-player.service: Audio stream player"
echo ""
echo "If no network is available, connect to WiFi hotspot 'bartix-config' (password: bartix-config)"
echo "Then access http://192.168.4.1:8080 to configure network settings"
echo ""
echo "View logs:"
echo "  journalctl -u network-manager.service -f"
echo "  journalctl -u config-server.service -f"
echo "  journalctl -u stream-player.service -f"