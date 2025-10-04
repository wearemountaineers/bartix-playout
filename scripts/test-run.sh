#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
echo “Usage: $0  [alsa-device]”
echo “Example: $0 https://alive-radio.s3.eu-west-1.amazonaws.com/stream.json alsa/plughw:CARD=Headphones,DEV=0”
exit 1
fi

MANIFEST_URL=”$1”
DEVICE=”${2:-alsa/plughw:CARD=Headphones,DEV=0}”

export STREAM_MANIFEST_URL=”$MANIFEST_URL”
export MPV_AUDIO_DEVICE=”$DEVICE”

python3 /usr/local/bin/bootstream.py