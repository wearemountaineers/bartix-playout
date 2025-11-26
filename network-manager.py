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
    check_count = 0
    while time.time() - start_time < timeout:
        has_ip, has_internet = has_network_connectivity()
        check_count += 1
        # Print progress every 5 seconds
        if check_count % 5 == 0:
            elapsed = int(time.time() - start_time)
            print(f"[network-manager] Waiting for network... ({elapsed}s/{timeout}s)", flush=True)
        
        if has_ip:
            if not check_internet or has_internet:
                elapsed = int(time.time() - start_time)
                print(f"[network-manager] Network detected after {elapsed}s", flush=True)
                return (has_ip, has_internet)
        time.sleep(1)
    
    # Return final status after timeout
    elapsed = int(time.time() - start_time)
    final_status = has_network_connectivity()
    print(f"[network-manager] Network wait timeout ({elapsed}s). Status: IP={final_status[0]}, Internet={final_status[1]}", flush=True)
    return final_status


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
        # Check if interface exists
        result = subprocess.run(
            ["ip", "link", "show", HOTSPOT_INTERFACE],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Error: Interface {HOTSPOT_INTERFACE} not found", flush=True)
            return False
        
        # Check if interface supports AP mode
        if not check_interface_supports_ap_mode(HOTSPOT_INTERFACE):
            print(f"[network-manager] Warning: Interface {HOTSPOT_INTERFACE} may not support AP mode", flush=True)
            # Continue anyway, hostapd will fail with a clearer error if not supported
        
        # Unblock WiFi if blocked (common on Raspberry Pi)
        print(f"[network-manager] Unblocking WiFi...", flush=True)
        subprocess.run(
            ["rfkill", "unblock", "wifi"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Stop wpa_supplicant if it's running (it conflicts with hostapd)
        print(f"[network-manager] Stopping wpa_supplicant...", flush=True)
        subprocess.run(
            ["systemctl", "stop", "wpa_supplicant"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Unmask hostapd if it's masked (required before starting)
        subprocess.run(
            ["systemctl", "unmask", HOSTAPD_SERVICE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Stop hostapd if it's already running (clean start)
        subprocess.run(
            ["systemctl", "stop", HOSTAPD_SERVICE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Stop dnsmasq if running
        subprocess.run(
            ["systemctl", "stop", DNSMASQ_SERVICE],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Ensure interface is down first
        print(f"[network-manager] Configuring interface {HOTSPOT_INTERFACE}...", flush=True)
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
        result = subprocess.run(
            ["ip", "addr", "add", f"{HOTSPOT_IP}/{HOTSPOT_NETMASK}", "dev", HOTSPOT_INTERFACE],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Warning: Failed to set IP: {result.stderr}", flush=True)
        
        result = subprocess.run(
            ["ip", "link", "set", HOTSPOT_INTERFACE, "up"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Error bringing interface up: {result.stderr}", flush=True)
            return False
        
        # Set maximum transmit power (20 dBm = 2000 mW) for better visibility
        print(f"[network-manager] Setting maximum transmit power...", flush=True)
        subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "set", "txpower", "fixed", "2000"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        time.sleep(2)
        
        # Start dnsmasq
        print(f"[network-manager] Starting dnsmasq...", flush=True)
        result = subprocess.run(
            ["systemctl", "start", DNSMASQ_SERVICE],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Error starting dnsmasq: {result.stderr}", flush=True)
            # Continue anyway, hostapd might still work
        
        time.sleep(1)
        
        # Start hostapd
        print(f"[network-manager] Starting hostapd...", flush=True)
        result = subprocess.run(
            ["systemctl", "start", HOSTAPD_SERVICE],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            error_msg = result.stderr or "Unknown error"
            print(f"[network-manager] Error starting hostapd: {error_msg}", flush=True)
            # Try to get more details from journalctl
            journal_result = subprocess.run(
                ["journalctl", "-u", HOSTAPD_SERVICE, "-n", "10", "--no-pager"],
                capture_output=True,
                text=True,
                check=False
            )
            if journal_result.returncode == 0:
                print(f"[network-manager] hostapd logs:\n{journal_result.stdout}", flush=True)
            return False
        
        time.sleep(2)
        
        if is_hotspot_running():
            print(f"[network-manager] Hotspot '{HOTSPOT_SSID}' started on {HOTSPOT_INTERFACE}", flush=True)
            return True
        else:
            print(f"[network-manager] Failed to start hotspot - service started but interface not in AP mode", flush=True)
            return False
    except Exception as e:
        print(f"[network-manager] Error starting hotspot: {e}", flush=True)
        import traceback
        print(f"[network-manager] Traceback: {traceback.format_exc()}", flush=True)
        return False


def check_interface_supports_ap_mode(interface):
    """Check if WiFi interface supports AP (access point) mode."""
    try:
        result = subprocess.run(
            ["iw", "dev", interface, "info"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Interface {interface} not found or not a WiFi device", flush=True)
            return False
        
        # Check if interface supports AP mode
        result = subprocess.run(
            ["iw", "phy", "phy0", "info"],
            capture_output=True,
            text=True,
            check=False
        )
        if "AP" in result.stdout or "* AP" in result.stdout:
            return True
        
        # Alternative check: try to set interface type
        result = subprocess.run(
            ["iw", "dev", interface, "set", "type", "ap"],
            capture_output=True,
            text=True,
            check=False
        )
        return result.returncode == 0
    except Exception as e:
        print(f"[network-manager] Error checking AP mode support: {e}", flush=True)
        return False


def ensure_hotspot_config():
    """
    Ensure hostapd and dnsmasq configuration files exist.
    Returns True if configs are ready.
    """
    # This will be called by install script to create configs
    # For now, just check if they exist
    if not os.path.exists(HOSTAPD_CONF):
        print(f"[network-manager] hostapd config not found: {HOSTAPD_CONF}", flush=True)
        return False
    if not os.path.exists(DNSMASQ_CONF):
        print(f"[network-manager] dnsmasq config not found: {DNSMASQ_CONF}", flush=True)
        return False
    return True


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
    
    # Set WiFi country code if not set (required for AP mode)
    try:
        result = subprocess.run(
            ["iw", "reg", "get"],
            capture_output=True,
            text=True,
            check=False
        )
        if "country 99" in result.stdout or "DFS-UNSET" in result.stdout:
            # Try to get country from wpa_supplicant.conf
            country_code = "NL"  # default
            try:
                if os.path.exists("/etc/wpa_supplicant/wpa_supplicant.conf"):
                    with open("/etc/wpa_supplicant/wpa_supplicant.conf", "r") as f:
                        for line in f:
                            if line.strip().startswith("country="):
                                country_code = line.split("=")[1].strip().upper()
                                break
            except Exception:
                pass
            
            print(f"[network-manager] Setting WiFi country code to {country_code}...", flush=True)
            subprocess.run(
                ["iw", "reg", "set", country_code],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except Exception as e:
        print(f"[network-manager] Warning: Could not set country code: {e}", flush=True)
    
    # Initial network check (with progress updates)
    print("[network-manager] Checking network connectivity...", flush=True)
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

