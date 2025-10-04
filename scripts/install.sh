#!/usr/bin/env bash
set -euo pipefail

usage() {
cat <<USAGE
Usage: $0 –user  –manifest  [–device ]

Examples:
sudo $0 –user admin 
–manifest https://alive-radio.s3.eu-west-1.amazonaws.com/stream.json 
–device “alsa/plughw:CARD=Headphones,DEV=0”
USAGE
}

USER_NAME=””
MANIFEST_URL=””
DEVICE=“alsa/plughw:CARD=Headphones,DEV=0”

while [[ $# -gt 0 ]]; do
case “$1” in
–user) USER_NAME=”$2”; shift 2;;
–manifest) MANIFEST_URL=”$2”; shift 2;;
–device) DEVICE=”$2”; shift 2;;
-h|–help) usage; exit 0;;
*) echo “Unknown arg: $1”; usage; exit 1;;
esac
done

[[ -z “$USER_NAME” || -z “$MANIFEST_URL” ]] && { usage; exit 1; }

#Packages

sudo apt-get update
sudo apt-get install -y python3 mpv ca-certificates alsa-utils

#Copy files

sudo install -m 0755 bootstream.py /usr/local/bin/bootstream.py
sudo mkdir -p /etc/systemd/system
sudo install -m 0644 systemd/stream-player.service /etc/systemd/system/stream-player.service

#Replace service placeholders

sudo sed -i “s#^User=.#User=${USER_NAME}#” /etc/systemd/system/stream-player.service
sudo sed -i “s#^Environment=STREAM_MANIFEST_URL=.#Environment=STREAM_MANIFEST_URL=${MANIFEST_URL}#” /etc/systemd/system/stream-player.service
sudo sed -i “s#^Environment=MPV_AUDIO_DEVICE=.*#Environment=MPV_AUDIO_DEVICE=${DEVICE}#” /etc/systemd/system/stream-player.service

#Ensure audio access

sudo usermod -aG audio “$USER_NAME” || true

#Enable + start

sudo systemctl daemon-reload
sudo systemctl enable stream-player.service
sudo systemctl restart stream-player.service

echo “\nInstalled. Use: journalctl -u stream-player.service -f”