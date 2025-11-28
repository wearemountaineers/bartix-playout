#!/usr/bin/env bash
set -euo pipefail

usage() {
cat <<USAGE
Usage: $0 [--remove-packages] [--remove-user-from-audio <username>]

Options:
  --remove-packages          Also remove installed packages (NetworkManager, etc.)
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

# Deactivate NetworkManager hotspot connection
echo "Deactivating hotspot connection..."
nmcli connection down Hotspot 2>/dev/null || true
nmcli connection delete Hotspot 2>/dev/null || true

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
sudo rm -rf /usr/local/share/bartix

# Remove NetworkManager WiFi client connection if it exists
echo "Removing NetworkManager WiFi client connection..."
nmcli connection delete WiFi-Client 2>/dev/null || true

# Remove virtual AP interface if it exists (NetworkManager may have created it)
echo "Removing virtual AP interface..."
if ip link show wlan0_ap >/dev/null 2>&1; then
    sudo ip link set wlan0_ap down 2>/dev/null || true
    sudo iw dev wlan0_ap del 2>/dev/null || true
    echo "Virtual AP interface wlan0_ap removed"
fi

# NetworkManager will continue to manage network normally
echo "NetworkManager will continue managing network connections"

# Remove user from audio group if requested
if [ -n "$REMOVE_USER" ]; then
    echo "Removing user $REMOVE_USER from audio group..."
    sudo deluser "$REMOVE_USER" audio 2>/dev/null || true
fi

# Remove packages if requested
if [ "$REMOVE_PACKAGES" = true ]; then
    echo "Removing packages..."
    echo "Warning: NetworkManager is a system service and may be used by other software."
    echo "Skipping NetworkManager removal to avoid breaking system networking."
    echo "To remove NetworkManager manually, run: sudo apt-get remove --purge network-manager"
    sudo apt-get remove --purge -y iw wireless-tools rfkill 2>/dev/null || true
    sudo apt-get autoremove -y 2>/dev/null || true
    echo "Note: python3, mpv, ca-certificates, alsa-utils, and network-manager were kept (may be used by other software)"
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


