#!/usr/bin/env bash
set -euo pipefail

usage() {
cat <<USAGE
Usage: $0 [--remove-packages] [--remove-user-from-audio <username>]

Options:
  --remove-packages          Also remove installed packages (hostapd, dnsmasq, etc.)
  --remove-user-from-audio    Remove user from audio group (specify username)

Examples:
  sudo $0
  sudo $0 --remove-packages
  sudo $0 --remove-user-from-audio admin
USAGE
}

REMOVE_PACKAGES=false
REMOVE_USER=""

while [[ $# -gt 0 ]]; do
case "$1" in
--remove-packages) REMOVE_PACKAGES=true; shift;;
--remove-user-from-audio) REMOVE_USER="$2"; shift 2;;
-h|--help) usage; exit 0;;
*) echo "Unknown arg: $1"; usage; exit 1;;
esac
done

echo "=========================================="
echo "Bartix Playout Uninstaller"
echo "=========================================="
echo ""

# Stop all services
echo "Stopping services..."
sudo systemctl stop stream-player.service 2>/dev/null || true
sudo systemctl stop network-manager.service 2>/dev/null || true
sudo systemctl stop config-server.service 2>/dev/null || true
sudo systemctl stop hostapd 2>/dev/null || true
sudo systemctl stop dnsmasq 2>/dev/null || true

# Disable services
echo "Disabling services..."
sudo systemctl disable stream-player.service 2>/dev/null || true
sudo systemctl disable network-manager.service 2>/dev/null || true
sudo systemctl disable config-server.service 2>/dev/null || true

# Remove systemd service files
echo "Removing systemd service files..."
sudo rm -f /etc/systemd/system/stream-player.service
sudo rm -f /etc/systemd/system/network-manager.service
sudo rm -f /etc/systemd/system/config-server.service
sudo systemctl daemon-reload

# Remove installed scripts
echo "Removing installed scripts..."
sudo rm -f /usr/local/bin/bootstream.py
sudo rm -f /usr/local/bin/network-manager.py
sudo rm -f /usr/local/bin/config-server.py
sudo rm -f /usr/local/bin/network-config.py

# Remove configuration files
echo "Removing configuration files..."
sudo rm -f /etc/hostapd/hostapd.conf
sudo rm -f /etc/dnsmasq.d/hotspot.conf
sudo rm -rf /usr/local/share/bartix

# Clean up network configuration
echo "Cleaning up network configuration..."
if [ -f /etc/dhcpcd.conf ]; then
    sudo sed -i '/denyinterfaces wlan0/d' /etc/dhcpcd.conf 2>/dev/null || true
fi

if [ -f /etc/default/hostapd ]; then
    sudo sed -i '/DAEMON_CONF="\/etc\/hostapd\/hostapd.conf"/d' /etc/default/hostapd 2>/dev/null || true
fi

# Restart network services to restore normal operation
echo "Restarting network services..."
sudo systemctl restart dhcpcd 2>/dev/null || true
sudo systemctl restart wpa_supplicant 2>/dev/null || true

# Remove user from audio group if requested
if [ -n "$REMOVE_USER" ]; then
    echo "Removing user $REMOVE_USER from audio group..."
    sudo deluser "$REMOVE_USER" audio 2>/dev/null || true
fi

# Remove packages if requested
if [ "$REMOVE_PACKAGES" = true ]; then
    echo "Removing packages..."
    sudo apt-get remove --purge -y hostapd dnsmasq iw wireless-tools rfkill 2>/dev/null || true
    sudo apt-get autoremove -y 2>/dev/null || true
    echo "Note: python3, mpv, ca-certificates, and alsa-utils were kept (may be used by other software)"
fi

echo ""
echo "=========================================="
echo "Uninstall complete!"
echo "=========================================="
echo ""
echo "All services have been stopped and removed."
echo "Configuration files have been cleaned up."
echo ""
echo "If you want to remove packages, run:"
echo "  sudo $0 --remove-packages"
echo ""

