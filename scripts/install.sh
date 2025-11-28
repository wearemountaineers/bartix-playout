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
sudo apt-get install -y python3 mpv ca-certificates alsa-utils network-manager iw wireless-tools rfkill

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

# NetworkManager will handle hotspot configuration - no config files needed

# Copy web template
sudo mkdir -p /usr/local/share/bartix/templates
sudo install -m 0644 templates/config.html /usr/local/share/bartix/templates/config.html

# Create directory for web password file
sudo mkdir -p /etc/bartix

# Generate unique SSID with random number (last 4 digits of MAC address or random)
UNIQUE_ID=""
if [ -e /sys/class/net/wlan0/address ]; then
    MAC=$(cat /sys/class/net/wlan0/address | tr -d ':')
    UNIQUE_ID="${MAC: -4}"
else
    # Fallback to random 4-digit number
    UNIQUE_ID=$(shuf -i 1000-9999 -n 1)
fi

HOTSPOT_SSID="bartix-config-${UNIQUE_ID}"
HOTSPOT_PASSWORD="bartix-${UNIQUE_ID}"

# Update network-manager.service with unique SSID, password, and interface
sudo sed -i "s|Environment=HOTSPOT_SSID=.*|Environment=HOTSPOT_SSID=${HOTSPOT_SSID}|" /etc/systemd/system/network-manager.service
sudo sed -i "s|Environment=HOTSPOT_PASSWORD=.*|Environment=HOTSPOT_PASSWORD=${HOTSPOT_PASSWORD}|" /etc/systemd/system/network-manager.service
sudo sed -i "s|Environment=HOTSPOT_INTERFACE=.*|Environment=HOTSPOT_INTERFACE=wlan0_ap|" /etc/systemd/system/network-manager.service

# Disable conflicting network services (NetworkManager will handle everything)
echo "Disabling conflicting network services..."
sudo systemctl disable dhcpcd || true
sudo systemctl stop dhcpcd || true
sudo systemctl disable hostapd || true
sudo systemctl stop hostapd || true
sudo systemctl disable dnsmasq || true
sudo systemctl stop dnsmasq || true
sudo systemctl disable wpa_supplicant || true
sudo systemctl stop wpa_supplicant || true

# Enable and start NetworkManager
echo "Enabling NetworkManager..."
sudo systemctl enable NetworkManager || true
sudo systemctl start NetworkManager || true

# Wait for NetworkManager to be ready
echo "Waiting for NetworkManager to be ready..."
for i in {1..10}; do
    if sudo systemctl is-active --quiet NetworkManager; then
        sleep 2
        if nmcli general status >/dev/null 2>&1; then
            echo "NetworkManager is ready"
            break
        fi
    fi
    sleep 1
done

# Configure NetworkManager to manage WiFi interfaces
echo "Configuring NetworkManager..."
# Ensure NetworkManager manages WiFi
sudo nmcli radio wifi on || true

# Set WiFi country code in NetworkManager
COUNTRY_CODE="${WIFI_COUNTRY:-NL}"
if [ -f /etc/wpa_supplicant/wpa_supplicant.conf ]; then
    EXISTING_COUNTRY=$(grep -i "^country=" /etc/wpa_supplicant/wpa_supplicant.conf | cut -d= -f2 | tr -d ' ' || echo "")
    if [ -n "$EXISTING_COUNTRY" ]; then
        COUNTRY_CODE="$EXISTING_COUNTRY"
    fi
fi

# Set country code via iw (immediate effect)
sudo iw reg set "$COUNTRY_CODE" 2>/dev/null || true

# NetworkManager will create the hotspot connection when network-manager.py starts
# No need to create it here - network-manager.py will handle it

sudo systemctl daemon-reload

# WiFi country code is already set above in NetworkManager configuration section

# Configure firewall to allow config server (if ufw is installed)
if command -v ufw >/dev/null 2>&1; then
    echo "Configuring firewall..."
    sudo ufw allow 8080/tcp comment "Bartix config server" 2>/dev/null || true
fi

# Set maximum WiFi transmit power for hotspot visibility
# This ensures the hotspot is visible even if country code isn't fully set
echo "Configuring WiFi transmit power..."
if command -v iw >/dev/null 2>&1 && [ -e /sys/class/net/wlan0 ]; then
    # Unblock WiFi first
    sudo rfkill unblock wifi 2>/dev/null || true
    sleep 1
    # Set maximum transmit power (20 dBm = 2000 mW)
    sudo iw dev wlan0 set txpower fixed 2000 2>/dev/null || true
    echo "WiFi transmit power set to maximum (20 dBm)"
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
            /usr/local/share/bartix/templates/config.html; do
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

# Ensure WiFi is unblocked and transmit power is set before starting services
if [ -e /sys/class/net/wlan0 ]; then
    sudo rfkill unblock wifi 2>/dev/null || true
    sleep 1
    sudo iw dev wlan0 set txpower fixed 2000 2>/dev/null || true
fi

sudo systemctl start network-manager.service
sleep 5  # Give network-manager more time to initialize and start hotspot

# Verify hotspot is actually broadcasting (NetworkManager will create it)
echo "Verifying hotspot is being created by NetworkManager..."
HOTSPOT_VERIFIED=false
for i in {1..5}; do
    sleep 2
    # Check if NetworkManager has created the hotspot connection
    if nmcli connection show "Hotspot" >/dev/null 2>&1; then
        # Check if interface exists and is in AP mode
        if ip link show wlan0_ap >/dev/null 2>&1; then
            if sudo iw dev wlan0_ap info 2>/dev/null | grep -q "type AP"; then
                HOTSPOT_VERIFIED=true
                break
            fi
        fi
    fi
done

if [ "$HOTSPOT_VERIFIED" = false ]; then
    echo "Warning: Hotspot may not be broadcasting yet. NetworkManager will create it when network-manager.py starts."
    echo "Check logs: sudo journalctl -u network-manager.service -n 50"
else
    echo "✓ Hotspot verified and broadcasting on wlan0_ap"
fi

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
echo "Hotspot SSID: ${HOTSPOT_SSID}"
echo "Hotspot Password: ${HOTSPOT_PASSWORD}"
echo ""
echo "If no network is available, connect to WiFi hotspot '${HOTSPOT_SSID}'"
echo "Then access http://192.168.4.1:8080 to configure network settings"
echo ""
echo "View logs:"
echo "  journalctl -u network-manager.service -f"
echo "  journalctl -u config-server.service -f"
echo "  journalctl -u stream-player.service -f"