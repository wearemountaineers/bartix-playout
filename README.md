# Bartix robust mp3 playout

A **robust, headless playout system** for Raspberry Pi that automatically starts at boot, fetches a JSON manifest with a stream URL, and plays an MP3 stream continuously — even after network failures or reboots.

Ideal for use as a **fixed playout server** (e.g., in retail, broadcast, or signage environments).

---

## ✨ Features

- **Auto-start at boot** via `systemd`
- **Manifest-driven configuration** (`stream_url` + optional volume)
- **Automatic retries** for network errors (infinite with backoff)
- **Periodic refresh** to switch streams remotely
- **Works headless (CLI only)** — no desktop needed
- **Automatic restarts** on failure
- **Supports HDMI or headphone output**
- **Uses mpv** for robust buffering and reconnect logic
- **Network fallback with WiFi hotspot** — automatically creates WiFi hotspot when network is unavailable
- **AP+STA concurrent support** — hotspot and WiFi client can run simultaneously using virtual AP interface
- **Web-based network configuration** — configure WiFi or LAN settings via web interface
- **Always-available hotspot** — hotspot remains active for remote access even when connected to WiFi network

---

## 🧩 Components

| File | Description |
|------|--------------|
| `bootstream.py` | Main Python supervisor that fetches manifest, starts player, monitors & restarts |
| `network-manager.py` | Network connectivity detection and WiFi hotspot management |
| `config-server.py` | Web server for network configuration interface |
| `network-config.py` | Utility to apply WiFi/LAN configuration changes |
| `systemd/stream-player.service` | Systemd service definition for auto-start and recovery |
| `systemd/network-manager.service` | Systemd service for network management and hotspot |
| `systemd/config-server.service` | Systemd service for web configuration interface |
| `config/hostapd.conf` | WiFi hotspot configuration template |
| `config/dnsmasq-hotspot.conf` | DHCP configuration for hotspot |
| `templates/config.html` | Web configuration form |
| `scripts/install.sh` | Installs dependencies, configures service, and enables it |
| `scripts/test-run.sh` | Simple test script to run manually (without systemd) |
| `stream.json` | Example JSON manifest file |
| `README.md` | Documentation |
| `LICENSE` | MIT license |

---

## 🧰 Installation

### 1️⃣ Clone or copy files

On your Raspberry Pi (running Raspberry Pi OS Lite or Desktop):

```bash
git clone https://github.com/wearemountaineers/bartix-playout
cd bartix-playout
```

or copy the ZIP and extract it:
```bash
unzip bartix-playout.zip
cd bartix-playout
```

---

### 2️⃣ Install the service

Run the installer (as root):

```bash
sudo bash scripts/install.sh --user admin \
  --manifest https://alive-radio.s3.eu-west-1.amazonaws.com/stream.json \
  --device "alsa/plughw:CARD=Headphones,DEV=0"
```

> 🧠 Tip:
> - Replace `admin` with your Pi username if different.
> - For HDMI audio, use `alsa/plughw:CARD=vc4hdmi,DEV=0`.

This installs:
- `bootstream.py`, `network-manager.py`, `config-server.py`, `network-config.py` to `/usr/local/bin`
- `stream-player.service`, `network-manager.service`, `config-server.service` to `/etc/systemd/system`
- WiFi hotspot configuration (`hostapd`, `dnsmasq`)
- Web configuration interface
- Enables and starts all services at boot

---

### 3️⃣ Reboot or start manually

```bash
sudo systemctl restart network-manager.service
sudo systemctl restart config-server.service
sudo systemctl restart stream-player.service
```

Check service status:
```bash
# Check all services
sudo systemctl status network-manager.service
sudo systemctl status config-server.service
sudo systemctl status stream-player.service

# View logs
journalctl -u stream-player.service -f
journalctl -u network-manager.service -f
journalctl -u config-server.service -f
```

You should see logs like:
```
[bootstream] Starting: mpv --no-video --ao=alsa --audio-device=alsa/plughw:1,0 ...
[network-manager] Hotspot 'bartix-config-XXXX' started and broadcasting on wlan0
[config-server] Configuration server started on port 8080
```
and hear your stream!

**After installation, you should see a WiFi hotspot** with SSID `bartix-config-XXXX`. Connect to it and access the web interface at `http://192.168.4.1:8080`.

---

## 📡 Manifest Format

Your JSON manifest should look like this:

```json
{
  "stream_url": "https://yourstreamserver.example.com:8000/live.mp3",
  "volume": 85
}
```

**Keys:**
- `stream_url` (required): MP3 stream URL
- `volume` (optional): 0–100 (sets ALSA mixer volume if available)

Example hosted on S3, HTTP, or any web server:
```
https://alive-radio.s3.eu-west-1.amazonaws.com/stream.json
```

---

## 🔊 Audio Configuration

List devices:
```bash
aplay -l
```

Typical device names:
- **Headphones:** `alsa/plughw:CARD=Headphones,DEV=0`
- **HDMI:** `alsa/plughw:CARD=vc4hdmi,DEV=0`

Set default output:
```bash
sudo raspi-config
# System Options → Audio → Select output
```

---

## ⚙️ Systemd Management

**Services:**
- `stream-player.service` - Main audio stream player
- `network-manager.service` - Network connectivity detection and WiFi hotspot management
- `config-server.service` - Web configuration interface

**Start services:**
```bash
sudo systemctl start network-manager.service
sudo systemctl start config-server.service
sudo systemctl start stream-player.service
```

**Check logs:**
```bash
journalctl -u stream-player.service -f
journalctl -u network-manager.service -f
journalctl -u config-server.service -f
```

**Enable on boot:**
```bash
sudo systemctl enable network-manager.service
sudo systemctl enable config-server.service
sudo systemctl enable stream-player.service
```

**Stop services:**
```bash
sudo systemctl stop stream-player.service
sudo systemctl stop config-server.service
sudo systemctl stop network-manager.service
```

**Restart all services:**
```bash
sudo systemctl restart network-manager.service
sudo systemctl restart config-server.service
sudo systemctl restart stream-player.service
```

---

## 🧪 Manual Testing (no systemd)

Run directly:
```bash
bash scripts/test-run.sh https://alive-radio.s3.eu-west-1.amazonaws.com/stream.json alsa/plughw:CARD=Headphones,DEV=0
```

Stops with `Ctrl + C`.

---

## 🧩 Troubleshooting

| Issue | Fix |
|-------|-----|
| `Playback open error: Unknown error 524` | Audio device not ready at boot — ensure ALSA device exists or adjust `ExecStartPre` wait in service file |
| No sound | Try HDMI vs headphone output, or run `amixer scontrols` and set proper mixer |
| DNS errors | Check internet or `/etc/resolv.conf` |
| Cert errors | `sudo apt install -y ca-certificates && sudo update-ca-certificates` |
| Audio muted | Run `amixer sset Headphone 90%` |
| Wrong device | Update `MPV_AUDIO_DEVICE` in service env vars |

---

## ⚡ Advanced Configuration

Edit service config:
```bash
sudo systemctl edit stream-player.service
```

You can override:
- `STREAM_MANIFEST_URL`
- `MPV_AUDIO_DEVICE`
- `DEFAULT_VOLUME`
- `MANIFEST_REFRESH_SEC`

Then reload:
```bash
sudo systemctl daemon-reload
sudo systemctl restart stream-player.service
```

---

## 📡 Network Fallback & Configuration

### WiFi Hotspot

The system uses **AP+STA concurrent mode** to support both WiFi client and hotspot simultaneously:

- **Physical Interface (wlan0)**: Used for WiFi client (STA mode) - connects to your WiFi network
- **Virtual Interface (wlan0_ap)**: Used for hotspot (AP mode) - provides configuration access point
- **SSID**: `bartix-config-XXXX` (where XXXX is a unique 4-digit ID based on MAC address)
- **Password**: `bartix-XXXX` (matches the unique ID)
- **IP Range**: `192.168.4.0/24`
- **Gateway**: `192.168.4.1`
- **Transmit Power**: Maximum (20 dBm) for best visibility
- **Regulatory Domain**: Automatically set (defaults to NL if not configured)

The hotspot is **always active** and runs concurrently with WiFi client connections. You can access the configuration interface via the hotspot even when connected to a WiFi network.

**Note**: The unique SSID ensures multiple bartix instances can run nearby without conflicts. The virtual AP interface (`wlan0_ap`) is automatically created on boot.

### Web Configuration Interface

When connected to the hotspot (or when the system has network), access the web configuration interface:

```
http://192.168.4.1:8080
```

**Authentication**: The web interface is password-protected. The default password is set during installation. You can change it via the System tab in the web interface.

**First-time Access**: If you haven't set a password yet, you may need to set one via the System tab before accessing other features.

The interface allows you to configure:

1. **WiFi Configuration**:
   - SSID (with network scanning)
   - Password (with connection testing)
   - Scan for available networks
   - Test WiFi connection before applying

2. **LAN Static IP Configuration**:
   - Static IP address
   - Subnet mask
   - Gateway
   - DNS server

3. **Hotspot Configuration**:
   - Hotspot SSID (with unique ID)
   - Hotspot password

4. **Manifest Management**:
   - Update manifest URL
   - Test manifest URL accessibility
   - Check current manifest URL

5. **Volume Control**:
   - Adjust system volume via slider

6. **System Logs**:
   - View logs for `network-manager` service
   - View logs for `config-server` service
   - View logs for `stream-player` service

7. **System Management**:
   - Reboot system
   - Set/change web interface password

After applying configuration, network services will restart automatically.

### Network Detection

The system automatically:
- Waits up to 30 seconds for network connectivity at boot
- Detects active IP addresses on all interfaces
- Tests internet connectivity
- Starts hotspot if no network is available
- Monitors network status continuously

### Troubleshooting Network Issues

| Issue | Solution |
|-------|----------|
| Can't connect to hotspot | Check WiFi adapter is enabled: `sudo rfkill unblock wifi` |
| Hotspot not visible/not broadcasting | Check logs: `journalctl -u network-manager.service -f`<br>Verify regulatory domain: `iw reg get`<br>Check transmit power: `iw dev wlan0 info \| grep txpower`<br>Set transmit power manually: `sudo iw dev wlan0 set txpower fixed 2000`<br>Restart hostapd: `sudo systemctl restart hostapd` |
| Hotspot not starting | Check logs: `journalctl -u network-manager.service -f`<br>Check hostapd logs: `journalctl -u hostapd -n 50`<br>Verify interface is available: `ip link show wlan0`<br>Check if hostapd is masked: `systemctl status hostapd` |
| Configuration not applying | Check network-config.py logs and verify file permissions |
| Can't access web interface | Ensure config-server is running: `sudo systemctl status config-server.service`<br>Check if port 8080 is accessible: `curl http://192.168.4.1:8080` |
| Can't scan for WiFi networks | When scanning for WiFi networks, the system temporarily pauses the hotspot. Use another device (phone, laptop) to scan if needed. |
| Regulatory domain not set | The system automatically sets the regulatory domain. If issues persist:<br>`sudo iw reg set NL` (or your country code)<br>Check current setting: `iw reg get` |
| Virtual AP interface missing | The virtual interface `wlan0_ap` is created automatically. If missing:<br>`sudo iw phy phy0 interface add wlan0_ap type __ap`<br>Verify: `ip link show wlan0_ap` |

**Diagnostic Commands:**

```bash
# Check hotspot status
sudo systemctl status hostapd
sudo systemctl status network-manager.service

# View detailed logs
sudo journalctl -u network-manager.service -f
sudo journalctl -u hostapd -n 50 --no-pager

# Check interface status
iw dev wlan0 info
ip link show wlan0

# Check regulatory domain and transmit power
iw reg get
iw dev wlan0 info | grep txpower

# Verify hotspot is broadcasting (from another device)
# Or check hostapd_cli status
sudo hostapd_cli -i wlan0 status
sudo hostapd_cli -i wlan0 get_config ssid

# Manually set transmit power if needed
sudo iw dev wlan0 set txpower fixed 2000
```

**Important Notes:**
- **Scanning Limitation**: When `wlan0` is in AP mode (hotspot active), you cannot use `iwlist wlan0 scan` or `iw dev wlan0 scan` from the same device. You must scan from another device (phone, laptop, etc.) or temporarily stop the hotspot.
- **Regulatory Domain**: The system automatically sets the regulatory domain based on `/etc/wpa_supplicant/wpa_supplicant.conf` or defaults to `NL`. This is critical for hotspot visibility.
- **Transmit Power**: The system sets transmit power to maximum (20 dBm = 2000 mW) for best visibility. If the hotspot is not visible, manually set it: `sudo iw dev wlan0 set txpower fixed 2000`.
- **Hotspot Verification**: The system uses `hostapd_cli` to verify the SSID is actually being broadcast. Check logs for verification status.

## 🗑️ Uninstallation

To remove the bartix playout system from your Raspberry Pi:

```bash
sudo bash scripts/uninstall.sh
```

This will:
- Stop and disable all services
- Remove all installed scripts and configuration files
- Clean up network configuration
- Restore normal network operation

**Options:**
- `--remove-packages`: Also remove installed packages (hostapd, dnsmasq, etc.)
- `--remove-user-from-audio <username>`: Remove user from audio group

**Examples:**
```bash
# Basic uninstall
sudo bash scripts/uninstall.sh

# Uninstall and remove packages
sudo bash scripts/uninstall.sh --remove-packages

# Uninstall and remove user from audio group
sudo bash scripts/uninstall.sh --remove-user-from-audio admin
```

---

## 🧾 License

MIT License  
© 2025 — Bart van den Berg / Mountaineers
See `LICENSE` for full terms.

---

## 💡 Credits

- Based on [mpv](https://mpv.io/) media player  
- Uses standard Raspberry Pi audio stack (ALSA)
- Built for 24/7 operation in headless setups
- Network fallback uses `hostapd` and `dnsmasq` for WiFi hotspot