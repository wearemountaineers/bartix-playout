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

[[ -z “$USER_NAME” || -z “$MANIFEST_URL” ]] && { usage; exit 1; }

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

# Copy hotspot configuration files
sudo mkdir -p /etc/hostapd
sudo install -m 0644 config/hostapd.conf /etc/hostapd/hostapd.conf
sudo mkdir -p /etc/dnsmasq.d
sudo install -m 0644 config/dnsmasq-hotspot.conf /etc/dnsmasq.d/hotspot.conf

# Copy web template
sudo mkdir -p /usr/local/share/bartix/templates
sudo install -m 0644 templates/config.html /usr/local/share/bartix/templates/config.html

# Configure hostapd to use our config
if [ -f /etc/default/hostapd ]; then
    sudo sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
    sudo sed -i 's|^DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
    # Ensure DAEMON_CONF is set
    if ! grep -q "^DAEMON_CONF=" /etc/default/hostapd; then
        echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' | sudo tee -a /etc/default/hostapd
    fi
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

#Replace service placeholders

sudo sed -i “s#^User=.#User=${USER_NAME}#” /etc/systemd/system/stream-player.service
sudo sed -i “s#^Environment=STREAM_MANIFEST_URL=.#Environment=STREAM_MANIFEST_URL=${MANIFEST_URL}#” /etc/systemd/system/stream-player.service
sudo sed -i “s#^Environment=MPV_AUDIO_DEVICE=.*#Environment=MPV_AUDIO_DEVICE=${DEVICE}#” /etc/systemd/system/stream-player.service

#Ensure audio access

sudo usermod -aG audio “$USER_NAME” || true

#Enable + start

sudo systemctl daemon-reload
sudo systemctl enable network-manager.service
sudo systemctl enable config-server.service
sudo systemctl enable stream-player.service

# Start network-manager first (it will start hotspot if needed)
sudo systemctl restart network-manager.service
sleep 2
sudo systemctl restart config-server.service
sleep 2
sudo systemctl restart stream-player.service

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