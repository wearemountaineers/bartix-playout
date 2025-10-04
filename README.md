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

---

## 🧩 Components

| File | Description |
|------|--------------|
| `bootstream.py` | Main Python supervisor that fetches manifest, starts player, monitors & restarts |
| `systemd/stream-player.service` | Systemd service definition for auto-start and recovery |
| `scripts/install.sh` | Installs dependencies, configures service, and enables it |
| `scripts/test-run.sh` | Simple test script to run manually (without systemd) |
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
- `bootstream.py` to `/usr/local/bin`
- `stream-player.service` to `/etc/systemd/system`
- Enables and starts the service at boot.

---

### 3️⃣ Reboot or start manually

```bash
sudo systemctl restart stream-player.service
journalctl -u stream-player.service -f
```

You should see logs like:
```
[bootstream] Starting: mpv --no-video --ao=alsa --audio-device=alsa/plughw:1,0 ...
```
and hear your stream!

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

Start immediately:
```bash
sudo systemctl start stream-player.service
```

Check logs:
```bash
journalctl -u stream-player.service -f
```

Enable on boot:
```bash
sudo systemctl enable stream-player.service
```

Stop:
```bash
sudo systemctl stop stream-player.service
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

## 🧾 License

MIT License  
© 2025 — Bart van den Berg / Mountaineers
See `LICENSE` for full terms.

---

## 💡 Credits

- Based on [mpv](https://mpv.io/) media player  
- Uses standard Raspberry Pi audio stack (ALSA)
- Built for 24/7 operation in headless setups