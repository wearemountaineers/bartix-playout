#!/usr/bin/env python3
"""
Network connectivity detection and WiFi hotspot management for Raspberry Pi.
- Detects active IP addresses and internet connectivity
- Manages WiFi hotspot when network is unavailable
- Monitors network status continuously
"""
import os
import sys
import time
import signal
import socket
import subprocess
import ipaddress
import urllib.request
import urllib.error

# Configuration
NETWORK_WAIT_TIMEOUT = int(os.environ.get("NETWORK_WAIT_TIMEOUT", "30"))
CONNECTIVITY_TEST_URL = os.environ.get("CONNECTIVITY_TEST_URL", "https://www.google.com")
CONNECTIVITY_TEST_TIMEOUT = int(os.environ.get("CONNECTIVITY_TEST_TIMEOUT", "5"))
HOTSPOT_SSID = os.environ.get("HOTSPOT_SSID", "bartix-config")
HOTSPOT_PASSWORD = os.environ.get("HOTSPOT_PASSWORD", "bartix-config")
HOTSPOT_INTERFACE = os.environ.get("HOTSPOT_INTERFACE", "wlan0")
HOTSPOT_IP = os.environ.get("HOTSPOT_IP", "192.168.4.1")
HOTSPOT_NETMASK = os.environ.get("HOTSPOT_NETMASK", "255.255.255.0")

# Paths
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
DNSMASQ_CONF = "/etc/dnsmasq.d/hotspot.conf"
HOSTAPD_SERVICE = "hostapd"
DNSMASQ_SERVICE = "dnsmasq"


def get_active_interfaces():
    """Get list of network interfaces with active IP addresses."""
    interfaces = []
    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show"],
            capture_output=True,
            text=True,
            check=False
        )
        current_if = None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith("inet "):
                # Interface name line (e.g., "2: eth0: ...")
                if ":" in line:
                    parts = line.split(":", 2)
                    if len(parts) >= 2:
                        current_if = parts[1].strip()
            elif line.startswith("inet "):
                # IP address line
                if current_if and current_if != "lo":
                    ip_part = line.split()[1].split("/")[0]
                    try:
                        ipaddress.IPv4Address(ip_part)
                        if current_if not in interfaces:
                            interfaces.append(current_if)
                    except ValueError:
                        pass
    except Exception as e:
        print(f"[network-manager] Error getting interfaces: {e}", flush=True)
    return interfaces


def has_ip_address(interface=None):
    """
    Check if a specific interface (or any interface) has an IP address.
    
    Args:
        interface: Interface name to check, or None to check any interface
    
    Returns:
        bool: True if IP address is found
    """
    interfaces = get_active_interfaces()
    if interface:
        return interface in interfaces
    return len(interfaces) > 0


def test_internet_connectivity():
    """
    Test internet connectivity by attempting to reach a test URL.
    
    Returns:
        bool: True if connectivity test succeeds
    """
    try:
        req = urllib.request.Request(
            CONNECTIVITY_TEST_URL,
            headers={"User-Agent": "network-manager/1.0"}
        )
        urllib.request.urlopen(req, timeout=CONNECTIVITY_TEST_TIMEOUT)
        return True
    except Exception:
        return False


def has_network_connectivity():
    """
    Check if system has network connectivity (IP address + internet).
    
    Returns:
        tuple: (has_ip, has_internet)
    """
    has_ip = has_ip_address()
    has_internet = False
    if has_ip:
        has_internet = test_internet_connectivity()
    return (has_ip, has_internet)


def wait_for_network(timeout=NETWORK_WAIT_TIMEOUT, check_internet=True):
    """
    Wait for network connectivity with timeout.
    
    Args:
        timeout: Maximum seconds to wait
        check_internet: If True, also check internet connectivity
    
    Returns:
        tuple: (has_ip, has_internet) - status at end of wait
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        has_ip, has_internet = has_network_connectivity()
        if has_ip:
            if not check_internet or has_internet:
                return (has_ip, has_internet)
        time.sleep(1)
    
    # Return final status after timeout
    return has_network_connectivity()


def is_hotspot_running():
    """Check if WiFi hotspot is currently running."""
    try:
        # Check if hostapd is running
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", HOSTAPD_SERVICE],
            check=False
        )
        if result.returncode == 0:
            return True
        
        # Also check if interface is in AP mode
        result = subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "info"],
            capture_output=True,
            text=True,
            check=False
        )
        if "type AP" in result.stdout:
            return True
    except Exception:
        pass
    return False


def stop_hotspot():
    """Stop WiFi hotspot."""
    try:
        subprocess.run(
            ["systemctl", "stop", HOSTAPD_SERVICE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        subprocess.run(
            ["systemctl", "stop", DNSMASQ_SERVICE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        # Bring down interface
        subprocess.run(
            ["ip", "link", "set", HOTSPOT_INTERFACE, "down"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print(f"[network-manager] Hotspot stopped", flush=True)
        return True
    except Exception as e:
        print(f"[network-manager] Error stopping hotspot: {e}", flush=True)
        return False


def start_hotspot():
    """Start WiFi hotspot."""
    try:
        # Ensure interface is down first
        subprocess.run(
            ["ip", "link", "set", HOTSPOT_INTERFACE, "down"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Configure interface with static IP
        subprocess.run(
            ["ip", "addr", "flush", "dev", HOTSPOT_INTERFACE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        subprocess.run(
            ["ip", "addr", "add", f"{HOTSPOT_IP}/{HOTSPOT_NETMASK}", "dev", HOTSPOT_INTERFACE],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        subprocess.run(
            ["ip", "link", "set", HOTSPOT_INTERFACE, "up"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        
        # Start dnsmasq
        subprocess.run(
            ["systemctl", "start", DNSMASQ_SERVICE],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Start hostapd
        subprocess.run(
            ["systemctl", "start", HOSTAPD_SERVICE],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        
        if is_hotspot_running():
            print(f"[network-manager] Hotspot '{HOTSPOT_SSID}' started on {HOTSPOT_INTERFACE}", flush=True)
            return True
        else:
            print(f"[network-manager] Failed to start hotspot", flush=True)
            return False
    except Exception as e:
        print(f"[network-manager] Error starting hotspot: {e}", flush=True)
        return False


def ensure_hotspot_config():
    """
    Ensure hostapd and dnsmasq configuration files exist.
    Returns True if configs are ready.
    """
    # This will be called by install script to create configs
    # For now, just check if they exist
    return os.path.exists(HOSTAPD_CONF) and os.path.exists(DNSMASQ_CONF)


def main_loop():
    """Main loop: monitor network and manage hotspot."""
    import signal
    
    stop_flag = False
    
    def signal_handler(sig, frame):
        nonlocal stop_flag
        stop_flag = True
        print(f"[network-manager] Signal {sig} received; stopping...", flush=True)
        stop_hotspot()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    print("[network-manager] Starting network manager...", flush=True)
    
    # Initial network check
    has_ip, has_internet = wait_for_network(timeout=NETWORK_WAIT_TIMEOUT, check_internet=False)
    
    # Always start hotspot (as per requirement: always available)
    if not is_hotspot_running():
        if ensure_hotspot_config():
            start_hotspot()
        else:
            print("[network-manager] Warning: Hotspot config files not found. Run install script.", flush=True)
    
    # Monitor loop
    last_check = time.time()
    check_interval = 10  # Check every 10 seconds
    
    while not stop_flag:
        try:
            now = time.time()
            if now - last_check >= check_interval:
                has_ip, has_internet = has_network_connectivity()
                
                # Ensure hotspot is running (always available requirement)
                if not is_hotspot_running():
                    if ensure_hotspot_config():
                        start_hotspot()
                
                last_check = now
            
            time.sleep(1)
        except KeyboardInterrupt:
            break
    
    stop_hotspot()
    print("[network-manager] Exiting.", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        # Test mode: check network status
        print("Checking network connectivity...", flush=True)
        has_ip, has_internet = has_network_connectivity()
        print(f"Has IP: {has_ip}, Has Internet: {has_internet}", flush=True)
        
        if has_ip:
            interfaces = get_active_interfaces()
            print(f"Active interfaces: {', '.join(interfaces) if interfaces else 'none'}", flush=True)
        
        print(f"Hotspot running: {is_hotspot_running()}", flush=True)
    else:
        main_loop()

