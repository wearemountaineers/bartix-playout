#!/usr/bin/env python3
"""
Boot-time playout supervisor for Raspberry Pi.
- Fetches a JSON manifest with a stream URL (+ optional volume)
- Starts an audio player (mpv by default)
- Monitors and restarts on failure
- Periodically refreshes the manifest so updates take effect
- Sends systemd watchdog heartbeats (Type=notify)

Env vars (via systemd or shell):
- STREAM_MANIFEST_URL: URL to JSON manifest (required if default not set)
- MPV_AUDIO_DEVICE: e.g. "alsa/plughw:CARD=Headphones,DEV=0" or "alsa/plughw:CARD=vc4hdmi,DEV=0"
- MANIFEST_REFRESH_SEC: seconds between manifest refreshes (default 300)
- HTTP_TIMEOUT_SEC: per-request timeout (default 6)
- DEFAULT_VOLUME: 0–100; used if manifest omits `volume` (or set to empty to skip)
"""
import json, os, sys, time, random, signal, subprocess, socket, shlex
import urllib.request, urllib.error

# ----------------- Configuration -----------------
MANIFEST_URL = os.environ.get(
    "STREAM_MANIFEST_URL",
    # You can hardcode a default here if you like:
    # "https://alive-radio.s3.eu-west-1.amazonaws.com/stream.json"
    None,
)
MPV_AUDIO_DEVICE = os.environ.get("MPV_AUDIO_DEVICE", "alsa/plughw:CARD=Headphones,DEV=0")
DEFAULT_VOL  = os.environ.get("DEFAULT_VOLUME", "")  # empty = don't touch volume
REFRESH_SEC  = int(os.environ.get("MANIFEST_REFRESH_SEC", "300"))
CONNECT_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT_SEC", "6"))
NETWORK_WAIT_TIMEOUT = int(os.environ.get("NETWORK_WAIT_TIMEOUT", "30"))

# systemd watchdog
WATCHDOG_USEC = int(os.environ.get("WATCHDOG_USEC", "0"))
WATCHDOG_SEC  = WATCHDOG_USEC / 1_000_000 if WATCHDOG_USEC else 0
NOTIFY_SOCKET = os.environ.get("NOTIFY_SOCKET")

# --------------------------------------------------

def sd_notify(msg: str):
    if not NOTIFY_SOCKET:
        return
    addr = NOTIFY_SOCKET
    if addr and addr[0] == "@":
        addr = "\0" + addr[1:]
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.connect(addr)
        s.sendall(msg.encode())
    except Exception:
        pass
    finally:
        s.close()


def jitter(base: float, pct: float = 0.2) -> float:
    return base * (1 + (random.random() * 2 - 1) * pct)


def fetch_manifest(url: str):
    if not url:
        raise RuntimeError("STREAM_MANIFEST_URL not set")
    delay = 2.0
    while True:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bootstream/1.0"})
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as r:
                return json.load(r)
        except Exception as e:
            print(f"[bootstream] Manifest fetch failed: {e}", flush=True)
            time.sleep(min(jitter(delay), 30))
            delay = min(delay * 1.7, 30)


def set_volume(pct):
    if pct is None or pct == "":
        return
    try:
        v = max(0, min(100, int(pct)))
        for ctl in ("PCM", "Headphone", "Speaker"):
            subprocess.run(["amixer", "set", ctl, f"{v}%"], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[bootstream] Volume set failed: {e}", flush=True)


def build_cmd(stream_url: str):
    """Build the player command. Default: mpv. Stable device naming."""
    device = os.environ.get("MPV_AUDIO_DEVICE", MPV_AUDIO_DEVICE)
    cmd = [
        "mpv", "--no-video", "--no-config",
        "--ao=alsa", f"--audio-device={device}",
        "--cache=yes", "--cache-secs=10",
        "--network-timeout=20",
        stream_url,
    ]
    return cmd

# If you insist on mpg123, comment build_cmd above and use this instead:
# def build_cmd(stream_url: str):
#     return [
#         "mpg123", "-q", "--timeout", "20", "-b", "2048", "-o", "alsa", "-a",
#         os.environ.get("AUDIO_DEVICE", "plughw:1,0"), stream_url
#     ]

child = None
stop_flag = False

def terminate_child():
    global child
    if child and child.poll() is None:
        try:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
        except Exception:
            pass
    child = None


def handle_signal(sig, frame):
    global stop_flag
    stop_flag = True
    print(f"[bootstream] Signal {sig} received; stopping…", flush=True)
    terminate_child()

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)


def wait_for_network_with_timeout(timeout=30):
    """
    Wait for network connectivity with timeout.
    Returns (has_ip, has_internet) tuple.
    """
    try:
        # Try to import network-manager functions
        sys.path.insert(0, os.path.dirname(__file__))
        try:
            # Import using importlib to handle hyphenated module name
            import importlib.util
            network_manager_path = os.path.join(os.path.dirname(__file__), "network-manager.py")
            if os.path.exists(network_manager_path):
                spec = importlib.util.spec_from_file_location("network_manager", network_manager_path)
                network_manager = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(network_manager)
                return network_manager.wait_for_network(timeout=timeout, check_internet=False)
        except ImportError:
            # Fallback: simple IP check
            import socket
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    # Try to get hostname (requires network)
                    socket.gethostbyname("localhost")
                    # Check for any IP on non-loopback interfaces
                    import subprocess
                    result = subprocess.run(
                        ["ip", "-4", "addr", "show"],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    if "inet " in result.stdout and "lo" not in result.stdout:
                        return (True, False)  # Has IP, internet unknown
                except Exception:
                    pass
                time.sleep(1)
            return (False, False)
    except Exception as e:
        print(f"[bootstream] Network check error: {e}", flush=True)
        return (False, False)


def main():
    global child

    sd_notify("READY=0")
    last_watchdog = time.time()

    # small grace to let NIC settle
    time.sleep(2)

    # Check for network connectivity before attempting manifest fetch
    print("[bootstream] Checking network connectivity...", flush=True)
    has_ip, has_internet = wait_for_network_with_timeout(timeout=NETWORK_WAIT_TIMEOUT)
    
    if not has_ip:
        print("[bootstream] Warning: No network connectivity detected. Hotspot should be available for configuration.", flush=True)
        print("[bootstream] Continuing anyway - will retry manifest fetch with backoff...", flush=True)
    else:
        print(f"[bootstream] Network detected (IP: {has_ip}, Internet: {has_internet})", flush=True)

    manifest = fetch_manifest(MANIFEST_URL)
    stream_url = manifest.get("stream_url")
    if not stream_url:
        print("[bootstream] Manifest missing 'stream_url'", flush=True)
        sys.exit(1)

    vol = manifest.get("volume", DEFAULT_VOL)
    if vol is not None and vol != "":
        set_volume(vol)

    next_refresh = time.time() + REFRESH_SEC

    sd_notify("READY=1")
    if WATCHDOG_SEC:
        sd_notify("WATCHDOG=1")

    while not stop_flag:
        now = time.time()
        if WATCHDOG_SEC and (now - last_watchdog) >= max(1.0, WATCHDOG_SEC / 2):
            sd_notify("WATCHDOG=1")
            last_watchdog = now

        if now >= next_refresh:
            try:
                new_manifest = fetch_manifest(MANIFEST_URL)
                new_url = new_manifest.get("stream_url") or stream_url
                if "volume" in new_manifest and new_manifest["volume"] is not None:
                    set_volume(new_manifest["volume"])
                if new_url != stream_url:
                    print("[bootstream] Stream URL changed; switching.", flush=True)
                    stream_url = new_url
                    terminate_child()
                next_refresh = time.time() + REFRESH_SEC
            except Exception as e:
                print(f"[bootstream] Manifest refresh error: {e}", flush=True)
                next_refresh = time.time() + 30

        if child is None or child.poll() is not None:
            if child is not None:
                code = child.poll()
                print(f"[bootstream] Player exited code {code}; restarting soon.", flush=True)
            cmd = build_cmd(stream_url)
            print(f"[bootstream] Starting: {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
            try:
                child = subprocess.Popen(cmd)
            except Exception as e:
                print(f"[bootstream] Failed to start player: {e}", flush=True)
                time.sleep(jitter(3.0))
                continue

        time.sleep(1)

    terminate_child()
    print("[bootstream] Exiting.", flush=True)

if __name__ == "__main__":
    main()