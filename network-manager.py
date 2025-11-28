#!/usr/bin/env python3
"""
Network connectivity detection and WiFi hotspot management for Raspberry Pi.
- Detects active IP addresses and internet connectivity
- Manages WiFi hotspot using NetworkManager
- Monitors network status continuously
"""
import os
import sys
import time
import signal
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
HOTSPOT_INTERFACE = os.environ.get("HOTSPOT_INTERFACE", "wlan0_ap")
HOTSPOT_CONNECTION_NAME = "Hotspot"

# Track if regulatory domain has been set (to avoid setting it repeatedly)
_regulatory_domain_set = False


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


def nmcli_run(cmd, check=True, capture_output=True):
    """Run nmcli command and return result."""
    full_cmd = ["nmcli"] + cmd
    result = subprocess.run(
        full_cmd,
        capture_output=capture_output,
        text=True,
        check=False
    )
    if check and result.returncode != 0:
        print(f"[network-manager] nmcli command failed: {' '.join(full_cmd)}", flush=True)
        if result.stderr:
            print(f"[network-manager] Error: {result.stderr}", flush=True)
    return result


def is_hotspot_running():
    """Check if WiFi hotspot is currently running via NetworkManager."""
    try:
        # Check if NetworkManager connection exists and is active
        result = nmcli_run(["connection", "show", HOTSPOT_CONNECTION_NAME], check=False)
        if result.returncode != 0:
            return False
        
        # Check if connection is active
        result = nmcli_run(["connection", "show", "--active", HOTSPOT_CONNECTION_NAME], check=False)
        if result.returncode == 0:
            # Verify interface is actually in AP mode
            result = subprocess.run(
                ["iw", "dev", HOTSPOT_INTERFACE, "info"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0 and "type AP" in result.stdout:
                return True
    except Exception as e:
        print(f"[network-manager] Error checking hotspot status: {e}", flush=True)
    return False


def verify_hotspot_broadcasting():
    """Verify that hotspot is actually broadcasting (not just configured)."""
    try:
        # Check if NetworkManager connection is active
        result = nmcli_run(["connection", "show", "--active", HOTSPOT_CONNECTION_NAME], check=False)
        if result.returncode != 0:
            print(f"[network-manager] Hotspot connection is not active", flush=True)
            return False
        
        # Check if interface exists and is in AP mode
        result = subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "info"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Cannot get interface info for {HOTSPOT_INTERFACE}", flush=True)
            return False
        
        if "type AP" not in result.stdout:
            print(f"[network-manager] Interface {HOTSPOT_INTERFACE} is not in AP mode", flush=True)
            return False
        
        # Check if interface is UP
        result = subprocess.run(
            ["ip", "link", "show", HOTSPOT_INTERFACE],
            capture_output=True,
            text=True,
            check=False
        )
        if "state UP" not in result.stdout:
            print(f"[network-manager] Interface {HOTSPOT_INTERFACE} is not UP", flush=True)
            return False
        
        # Check transmit power
        result = subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "info"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0 and result.stdout:
            try:
                import re
                match = re.search(r'txpower\s+(\d+(?:\.\d+)?)\s*dBm', result.stdout, re.IGNORECASE)
                if match:
                    txpower = float(match.group(1))
                    if txpower < 10:
                        print(f"[network-manager] Warning: Transmit power is low ({txpower} dBm)", flush=True)
            except Exception:
                pass
        
        return True
    except Exception as e:
        print(f"[network-manager] Error verifying hotspot: {e}", flush=True)
        import traceback
        print(f"[network-manager] Traceback: {traceback.format_exc()}", flush=True)
        return False


def stop_hotspot():
    """Stop WiFi hotspot by deactivating NetworkManager connection."""
    try:
        # Deactivate the hotspot connection
        result = nmcli_run(["connection", "down", HOTSPOT_CONNECTION_NAME], check=False)
        if result.returncode == 0:
            print(f"[network-manager] Hotspot stopped", flush=True)
        else:
            # Connection might not exist or already be down
            print(f"[network-manager] Hotspot connection not active or doesn't exist", flush=True)
        return True
    except Exception as e:
        print(f"[network-manager] Error stopping hotspot: {e}", flush=True)
        return False


def start_hotspot():
    """Start WiFi hotspot using NetworkManager."""
    try:
        # Unblock WiFi if blocked
        print(f"[network-manager] Unblocking WiFi...", flush=True)
        subprocess.run(
            ["rfkill", "unblock", "wifi"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(1)
        
        # Check if connection already exists
        result = nmcli_run(["connection", "show", HOTSPOT_CONNECTION_NAME], check=False)
        connection_exists = (result.returncode == 0)
        
        if not connection_exists:
            print(f"[network-manager] Creating hotspot connection '{HOTSPOT_CONNECTION_NAME}'...", flush=True)
            # Create hotspot connection
            # NetworkManager will automatically create wlan0_ap interface if needed
            result = nmcli_run([
                "connection", "add",
                "type", "wifi",
                "ifname", HOTSPOT_INTERFACE,
                "con-name", HOTSPOT_CONNECTION_NAME,
                "autoconnect", "yes",
                "ssid", HOTSPOT_SSID,
                "mode", "ap",
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", HOTSPOT_PASSWORD,
                "ipv4.method", "shared"
            ])
            
            if result.returncode != 0:
                print(f"[network-manager] Error creating hotspot connection: {result.stderr}", flush=True)
                return False
            print(f"[network-manager] Hotspot connection created", flush=True)
        else:
            # Update connection if SSID or password changed
            print(f"[network-manager] Hotspot connection exists, updating if needed...", flush=True)
            nmcli_run(["connection", "modify", HOTSPOT_CONNECTION_NAME, "ssid", HOTSPOT_SSID], check=False)
            nmcli_run(["connection", "modify", HOTSPOT_CONNECTION_NAME, "wifi-sec.psk", HOTSPOT_PASSWORD], check=False)
        
        # Set regulatory domain if needed (only once)
        global _regulatory_domain_set
        if not _regulatory_domain_set:
            reg_result = subprocess.run(
                ["iw", "reg", "get"],
                capture_output=True,
                text=True,
                check=False
            )
            if reg_result.returncode == 0 and ("country 99" in reg_result.stdout or "DFS-UNSET" in reg_result.stdout):
                country_code = "NL"  # default
                try:
                    # Try to get country from NetworkManager or system
                    nm_result = nmcli_run(["general", "permissions"], check=False)
                    # Or check /etc/default/crda or similar
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
                _regulatory_domain_set = True
                time.sleep(1)
        
        # Activate the hotspot connection
        print(f"[network-manager] Activating hotspot connection...", flush=True)
        result = nmcli_run(["connection", "up", HOTSPOT_CONNECTION_NAME])
        
        if result.returncode != 0:
            print(f"[network-manager] Error activating hotspot: {result.stderr}", flush=True)
            return False
        
        # Wait a bit for connection to establish
        time.sleep(3)
        
        # Verify hotspot is broadcasting
        print(f"[network-manager] Verifying hotspot is broadcasting...", flush=True)
        if verify_hotspot_broadcasting():
            print(f"[network-manager] Hotspot '{HOTSPOT_SSID}' started and broadcasting on {HOTSPOT_INTERFACE}", flush=True)
            
            # Check transmit power
            result = subprocess.run(
                ["iw", "dev", HOTSPOT_INTERFACE, "info"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0 and result.stdout:
                import re
                match = re.search(r'txpower\s+(\d+(?:\.\d+)?)\s*dBm', result.stdout, re.IGNORECASE)
                if match:
                    txpower = match.group(1)
                    print(f"[network-manager] Transmit power: {txpower} dBm", flush=True)
            
            return True
        else:
            print(f"[network-manager] Warning: Hotspot connection activated but not broadcasting", flush=True)
            return False
            
    except Exception as e:
        print(f"[network-manager] Error starting hotspot: {e}", flush=True)
        import traceback
        print(f"[network-manager] Traceback: {traceback.format_exc()}", flush=True)
        return False


def ensure_hotspot_config():
    """
    Ensure NetworkManager is running and ready.
    Returns True if NetworkManager is ready.
    """
    try:
        # Check if NetworkManager is running
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "NetworkManager"],
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] NetworkManager service is not active", flush=True)
            return False
        
        # Check if nmcli is available
        result = nmcli_run(["general", "status"], check=False)
        if result.returncode != 0:
            print(f"[network-manager] NetworkManager is not responding", flush=True)
            return False
        
        return True
    except Exception as e:
        print(f"[network-manager] Error checking NetworkManager: {e}", flush=True)
        return False


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
    
    # Wait for NetworkManager to be ready
    print("[network-manager] Waiting for NetworkManager to be ready...", flush=True)
    for i in range(30):
        if ensure_hotspot_config():
            print("[network-manager] NetworkManager is ready", flush=True)
            break
        time.sleep(1)
    else:
        print("[network-manager] Warning: NetworkManager not ready after 30s, continuing anyway", flush=True)
    
    # Wait for physical WiFi interface (wlan0) to be available
    print("[network-manager] Waiting for physical WiFi interface (wlan0) to be available...", flush=True)
    interface_wait_timeout = 30
    wlan0_available = False
    for i in range(interface_wait_timeout):
        try:
            result = subprocess.run(
                ["ip", "link", "show", "wlan0"],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0:
                wlan0_available = True
                print(f"[network-manager] Physical WiFi interface wlan0 is available", flush=True)
                break
        except Exception:
            pass
        if i < interface_wait_timeout - 1:
            time.sleep(1)
    
    if not wlan0_available:
        print(f"[network-manager] Warning: Physical WiFi interface wlan0 not found after {interface_wait_timeout}s", flush=True)
        print("[network-manager] Will continue and retry in monitor loop", flush=True)
    
    # Set WiFi country code if not set (required for AP mode)
    try:
        result = subprocess.run(
            ["iw", "reg", "get"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            if "country 99" in result.stdout or "DFS-UNSET" in result.stdout:
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
                global _regulatory_domain_set
                _regulatory_domain_set = True
                time.sleep(2)
    except Exception as e:
        print(f"[network-manager] Warning: Could not set country code: {e}", flush=True)
    
    # Initial network check (with progress updates)
    print("[network-manager] Checking network connectivity...", flush=True)
    has_ip, has_internet = wait_for_network(timeout=NETWORK_WAIT_TIMEOUT, check_internet=False)
    
    # Wait a bit more for system to fully settle after boot
    print("[network-manager] Waiting for system to settle...", flush=True)
    time.sleep(3)
    
    # Always start hotspot (as per requirement: always available)
    print("[network-manager] Ensuring hotspot is started...", flush=True)
    max_retries = 3
    hotspot_started = False
    
    for attempt in range(max_retries):
        if is_hotspot_running() and verify_hotspot_broadcasting():
            print("[network-manager] Hotspot already running and broadcasting", flush=True)
            hotspot_started = True
            break
        
        if ensure_hotspot_config():
            if start_hotspot():
                # Verify it's actually broadcasting
                time.sleep(2)
                if verify_hotspot_broadcasting():
                    hotspot_started = True
                    break
                else:
                    print(f"[network-manager] Hotspot started but not broadcasting, retry {attempt + 1}/{max_retries}...", flush=True)
            else:
                print(f"[network-manager] Failed to start hotspot, retry {attempt + 1}/{max_retries}...", flush=True)
        else:
            print("[network-manager] Warning: NetworkManager not ready. Run install script.", flush=True)
            break
        
        if attempt < max_retries - 1:
            time.sleep(5)  # Wait before retry
    
    if not hotspot_started:
        print("[network-manager] Warning: Hotspot not started after retries, will continue monitoring", flush=True)
    
    # Monitor loop
    last_check = time.time()
    check_interval = 10  # Check every 10 seconds
    
    while not stop_flag:
        try:
            now = time.time()
            if now - last_check >= check_interval:
                has_ip, has_internet = has_network_connectivity()
                
                # Ensure hotspot is running and broadcasting (always available requirement)
                # NetworkManager handles AP+STA concurrent mode automatically
                
                # Try to start/restart hotspot
                if not is_hotspot_running() or not verify_hotspot_broadcasting():
                    if not is_hotspot_running():
                        # Start if not running
                        if ensure_hotspot_config():
                            start_hotspot()
                    else:
                        # Connection is active but not broadcasting - try reactivating
                        print("[network-manager] Hotspot connection active but not broadcasting, reactivating...", flush=True)
                        stop_hotspot()
                        time.sleep(2)
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
