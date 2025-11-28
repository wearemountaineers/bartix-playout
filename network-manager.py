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
HOTSPOT_INTERFACE = os.environ.get("HOTSPOT_INTERFACE", "wlan0_ap")
HOTSPOT_IP = os.environ.get("HOTSPOT_IP", "192.168.4.1")
HOTSPOT_NETMASK = os.environ.get("HOTSPOT_NETMASK", "255.255.255.0")

# Paths
HOSTAPD_CONF = "/etc/hostapd/hostapd.conf"
DNSMASQ_CONF = "/etc/dnsmasq.d/hotspot.conf"
HOSTAPD_SERVICE = "hostapd"
DNSMASQ_SERVICE = "dnsmasq"

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


def is_hotspot_running():
    """Check if WiFi hotspot is currently running."""
    try:
        # Check if hostapd is running
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", HOSTAPD_SERVICE],
            check=False
        )
        if result.returncode == 0:
            # Also verify interface is actually in AP mode
            result = subprocess.run(
                ["iw", "dev", HOTSPOT_INTERFACE, "info"],
                capture_output=True,
                text=True,
                check=False
            )
            if "type AP" in result.stdout:
                # Also check if dnsmasq is running (required for DHCP)
                dnsmasq_result = subprocess.run(
                    ["systemctl", "is-active", "--quiet", DNSMASQ_SERVICE],
                    check=False
                )
                if dnsmasq_result.returncode == 0:
                    return True
                else:
                    print(f"[network-manager] Warning: dnsmasq is not running (DHCP will not work)", flush=True)
                    return False
    except Exception:
        pass
    return False


def verify_hotspot_broadcasting():
    """Verify that hotspot is actually broadcasting (not just running)."""
    try:
        # Check if hostapd service is active first
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", HOSTAPD_SERVICE],
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] hostapd service is not active", flush=True)
            return False
        
        # Check if interface is up and in AP mode
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
            print(f"[network-manager] Interface info: {result.stdout}", flush=True)
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
        
        # Check transmit power (should be set to max) - use 'info' command instead of 'get txpower'
        result = subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "info"],
            capture_output=True,
            text=True,
            check=False
        )
        # If txpower is very low or not set, hotspot might not be visible
        if result.returncode == 0 and result.stdout:
            try:
                # Extract dBm value from info output (format: "txpower 20.00 dBm" or similar)
                import re
                match = re.search(r'txpower\s+(\d+(?:\.\d+)?)\s*dBm', result.stdout, re.IGNORECASE)
                if match:
                    txpower = float(match.group(1))
                    if txpower < 10:  # Very low power, might not be visible
                        print(f"[network-manager] Warning: Transmit power is low ({txpower} dBm), setting to maximum", flush=True)
                        subprocess.run(
                            ["iw", "dev", HOTSPOT_INTERFACE, "set", "txpower", "fixed", "2000"],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                else:
                    print(f"[network-manager] Warning: Could not parse transmit power from interface info", flush=True)
            except Exception as e:
                print(f"[network-manager] Warning: Error checking transmit power: {e}", flush=True)
        
        # Verify with hostapd_cli that SSID is actually being broadcast
        # Note: hostapd_cli requires control interface to be configured in hostapd.conf
        # If control interface is not configured, this check will fail but that's OK
        result = subprocess.run(
            ["hostapd_cli", "-i", HOTSPOT_INTERFACE, "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3
        )
        if result.returncode == 0:
            status_output = result.stdout
            # Check if SSID is in the status
            if HOTSPOT_SSID in status_output or "state=ENABLED" in status_output:
                print(f"[network-manager] hostapd_cli confirms hotspot is enabled", flush=True)
            else:
                print(f"[network-manager] Warning: hostapd_cli status doesn't show expected state", flush=True)
                print(f"[network-manager] hostapd_cli output: {status_output}", flush=True)
        else:
            # hostapd_cli failure is not critical - it might be a config issue or timing
            # The important checks (interface in AP mode, hostapd service active) already passed
            if "No such file or directory" in result.stderr or "Failed to connect" in result.stderr:
                print(f"[network-manager] Note: hostapd_cli cannot connect (control interface may not be configured)", flush=True)
                print(f"[network-manager] This is not critical - hotspot may still be working. Check interface status above.", flush=True)
            else:
                print(f"[network-manager] Warning: hostapd_cli check failed (return code {result.returncode})", flush=True)
                print(f"[network-manager] hostapd_cli error: {result.stderr}", flush=True)
        
        # Regulatory domain check removed from verification function
        # Setting regulatory domain repeatedly interferes with WiFi client setup
        # It should only be set during hotspot startup, not during verification
        # The regulatory domain is set in start_hotspot() and main_loop() initialization
        
        return True
    except Exception as e:
        print(f"[network-manager] Error verifying hotspot: {e}", flush=True)
        import traceback
        print(f"[network-manager] Traceback: {traceback.format_exc()}", flush=True)
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
        # Check if interface exists, create it if it's the virtual AP interface
        result = subprocess.run(
            ["ip", "link", "show", HOTSPOT_INTERFACE],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            if HOTSPOT_INTERFACE == "wlan0_ap":
                # Try to create the virtual AP interface
                print(f"[network-manager] Virtual AP interface {HOTSPOT_INTERFACE} not found, creating...", flush=True)
                if not create_virtual_ap_interface(HOTSPOT_INTERFACE):
                    print(f"[network-manager] Error: Could not create virtual AP interface {HOTSPOT_INTERFACE}", flush=True)
                    return False
            else:
                print(f"[network-manager] Error: Interface {HOTSPOT_INTERFACE} not found", flush=True)
                return False
        
        # With AP+STA concurrent support, wlan0_ap is a virtual interface dedicated to AP mode
        # wlan0 can be in managed mode (STA) while wlan0_ap is in AP mode - they don't conflict
        # No need to check for managed mode on the hotspot interface
        
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
        
        # With AP+STA concurrent support, wpa_supplicant (on wlan0) and hostapd (on wlan0_ap) don't conflict
        # No need to stop wpa_supplicant
        
        # CRITICAL: Add denyinterfaces wlan0_ap to dhcpcd.conf
        # This prevents dhcpcd from managing the virtual AP interface (we set static IP for hotspot)
        # Note: wlan0 should NOT be denied - it's used for STA mode and needs dhcpcd
        print(f"[network-manager] Preventing dhcpcd from managing {HOTSPOT_INTERFACE}...", flush=True)
        dhcpcd_conf = "/etc/dhcpcd.conf"
        if os.path.exists(dhcpcd_conf):
            with open(dhcpcd_conf, 'r') as f:
                dhcpcd_lines = f.readlines()
            
            # Check if denyinterfaces wlan0_ap already exists
            has_deny = False
            for line in dhcpcd_lines:
                if f"denyinterfaces {HOTSPOT_INTERFACE}" in line.strip():
                    has_deny = True
                    break
            
            if not has_deny:
                # Add denyinterfaces wlan0_ap
                with open(dhcpcd_conf, 'a') as f:
                    f.write(f"denyinterfaces {HOTSPOT_INTERFACE}\n")
                print(f"[network-manager] Added 'denyinterfaces {HOTSPOT_INTERFACE}' to dhcpcd.conf", flush=True)
                
                # Restart dhcpcd to apply the change
                subprocess.run(
                    ["systemctl", "restart", "dhcpcd"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                time.sleep(1)
            else:
                print(f"[network-manager] 'denyinterfaces {HOTSPOT_INTERFACE}' already in dhcpcd.conf", flush=True)
        
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
        
        # Regulatory domain should only be set once during initialization
        # Setting it repeatedly interferes with WiFi client connections
        # It's set in main_loop() initialization, so skip here to avoid interference
        global _regulatory_domain_set
        if not _regulatory_domain_set:
            # Only set if completely unset (first time only)
            reg_result = subprocess.run(
                ["iw", "reg", "get"],
                capture_output=True,
                text=True,
                check=False
            )
            if reg_result.returncode == 0 and ("country 99" in reg_result.stdout or "DFS-UNSET" in reg_result.stdout):
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
                
                print(f"[network-manager] Setting WiFi country code to {country_code} (first time only)...", flush=True)
                subprocess.run(
                    ["iw", "reg", "set", country_code],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                _regulatory_domain_set = True
                time.sleep(1)
        
        # Set maximum transmit power (20 dBm = 2000 mW) for better visibility
        print(f"[network-manager] Setting maximum transmit power...", flush=True)
        subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "set", "txpower", "fixed", "2000"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Set transmit power again after bringing interface up (ensures it's set)
        time.sleep(1)
        print(f"[network-manager] Setting maximum transmit power (retry)...", flush=True)
        result = subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "set", "txpower", "fixed", "2000"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Warning: Failed to set txpower: {result.stderr}", flush=True)
        else:
            # Verify it was set by checking interface info
            check_result = subprocess.run(
                ["iw", "dev", HOTSPOT_INTERFACE, "info"],
                capture_output=True,
                text=True,
                check=False
            )
            if check_result.returncode == 0:
                import re
                match = re.search(r'txpower\s+(\d+(?:\.\d+)?)\s*dBm', check_result.stdout, re.IGNORECASE)
                if match:
                    txpower = match.group(1)
                    print(f"[network-manager] Transmit power verified: {txpower} dBm", flush=True)
                else:
                    print(f"[network-manager] Warning: Could not verify transmit power in interface info", flush=True)
                    print(f"[network-manager] Interface info: {check_result.stdout}", flush=True)
        
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
            # Try to get more details from journalctl
            journal_result = subprocess.run(
                ["journalctl", "-u", DNSMASQ_SERVICE, "-n", "10", "--no-pager"],
                capture_output=True,
                text=True,
                check=False
            )
            if journal_result.returncode == 0:
                print(f"[network-manager] dnsmasq logs:\n{journal_result.stdout}", flush=True)
        else:
            # Verify dnsmasq is actually running
            time.sleep(1)
            verify_result = subprocess.run(
                ["systemctl", "is-active", "--quiet", DNSMASQ_SERVICE],
                check=False
            )
            if verify_result.returncode == 0:
                print(f"[network-manager] dnsmasq started successfully", flush=True)
            else:
                print(f"[network-manager] Warning: dnsmasq start command succeeded but service is not active", flush=True)
                # Try to get status
                status_result = subprocess.run(
                    ["systemctl", "status", DNSMASQ_SERVICE, "--no-pager", "-n", "10"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if status_result.returncode == 0:
                    print(f"[network-manager] dnsmasq status:\n{status_result.stdout}", flush=True)
        
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
        
        # After hostapd starts, verify dnsmasq is still running and restart if needed
        # This is important because dnsmasq might fail to bind if started before interface is ready
        time.sleep(2)
        dnsmasq_check = subprocess.run(
            ["systemctl", "is-active", "--quiet", DNSMASQ_SERVICE],
            check=False
        )
        if dnsmasq_check.returncode != 0:
            print(f"[network-manager] dnsmasq not running after hostapd start, restarting...", flush=True)
            restart_result = subprocess.run(
                ["systemctl", "restart", DNSMASQ_SERVICE],
                capture_output=True,
                text=True,
                check=False
            )
            time.sleep(1)
            # Verify again
            dnsmasq_check2 = subprocess.run(
                ["systemctl", "is-active", "--quiet", DNSMASQ_SERVICE],
                check=False
            )
            if dnsmasq_check2.returncode == 0:
                print(f"[network-manager] dnsmasq restarted successfully", flush=True)
            else:
                print(f"[network-manager] Error: dnsmasq failed to start after restart", flush=True)
                if restart_result.stderr:
                    print(f"[network-manager] dnsmasq restart error: {restart_result.stderr}", flush=True)
                # Get dnsmasq logs for debugging
                journal_result = subprocess.run(
                    ["journalctl", "-u", DNSMASQ_SERVICE, "-n", "20", "--no-pager"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if journal_result.returncode == 0:
                    print(f"[network-manager] dnsmasq logs:\n{journal_result.stdout}", flush=True)
        
        time.sleep(4)  # Give hostapd more time to initialize
        
        # Set transmit power one more time after hostapd starts (sometimes it gets reset)
        print(f"[network-manager] Re-setting transmit power after hostapd start...", flush=True)
        subprocess.run(
            ["iw", "dev", HOTSPOT_INTERFACE, "set", "txpower", "fixed", "2000"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(2)
        
        # Check hostapd logs for any errors
        journal_result = subprocess.run(
            ["journalctl", "-u", HOSTAPD_SERVICE, "-n", "20", "--no-pager"],
            capture_output=True,
            text=True,
            check=False
        )
        if journal_result.returncode == 0:
            if "error" in journal_result.stdout.lower() or "failed" in journal_result.stdout.lower():
                print(f"[network-manager] Warning: hostapd logs show errors:", flush=True)
                print(f"{journal_result.stdout}", flush=True)
            else:
                print(f"[network-manager] hostapd logs look good", flush=True)
        
        # Verify hotspot is actually broadcasting
        print(f"[network-manager] Verifying hotspot is broadcasting...", flush=True)
        if verify_hotspot_broadcasting():
            # Double-check by verifying transmit power is set
            result = subprocess.run(
                ["iw", "dev", HOTSPOT_INTERFACE, "info"],
                capture_output=True,
                text=True,
                check=False
            )
            print(f"[network-manager] Hotspot '{HOTSPOT_SSID}' started and broadcasting on {HOTSPOT_INTERFACE}", flush=True)
            if result.returncode == 0 and result.stdout:
                import re
                match = re.search(r'txpower\s+(\d+(?:\.\d+)?)\s*dBm', result.stdout, re.IGNORECASE)
                if match:
                    txpower = match.group(1)
                    print(f"[network-manager] Transmit power: {txpower} dBm", flush=True)
            
            # Additional verification: check hostapd status and SSID
            result = subprocess.run(
                ["hostapd_cli", "-i", HOTSPOT_INTERFACE, "status"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3
            )
            if result.returncode == 0:
                print(f"[network-manager] hostapd status: {result.stdout.strip()}", flush=True)
                # Try to get the SSID from hostapd
                # Note: hostapd_cli get_config ssid returns multiple lines like:
                # bssid=b8:27:eb:45:48:9a
                # ssid=bartix-config-489a
                # wps_state=disabled
                # ...
                ssid_result = subprocess.run(
                    ["hostapd_cli", "-i", HOTSPOT_INTERFACE, "get_config", "ssid"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2
                )
                if ssid_result.returncode == 0:
                    # Parse the output - extract SSID from the multi-line response
                    actual_ssid = None
                    for line in ssid_result.stdout.strip().split('\n'):
                        line = line.strip()
                        if line.startswith('ssid='):
                            actual_ssid = line.split('=', 1)[1].strip()
                            break
                    
                    if actual_ssid:
                        print(f"[network-manager] Hotspot SSID verified: {actual_ssid}", flush=True)
                        if actual_ssid != HOTSPOT_SSID:
                            print(f"[network-manager] Warning: SSID mismatch! Expected '{HOTSPOT_SSID}', got '{actual_ssid}'", flush=True)
                    else:
                        print(f"[network-manager] Note: Could not parse SSID from hostapd_cli output", flush=True)
                        print(f"[network-manager] hostapd_cli output: {ssid_result.stdout[:200]}", flush=True)
            else:
                print(f"[network-manager] Warning: Could not get hostapd_cli status (return code {result.returncode})", flush=True)
                print(f"[network-manager] hostapd_cli error: {result.stderr}", flush=True)
            
            return True
        else:
            print(f"[network-manager] Warning: hostapd started but hotspot not broadcasting. Attempting restart...", flush=True)
            # Try restarting hostapd once
            subprocess.run(
                ["systemctl", "restart", HOSTAPD_SERVICE],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(3)
            
            # Re-verify transmit power
            subprocess.run(
                ["iw", "dev", HOTSPOT_INTERFACE, "set", "txpower", "fixed", "2000"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)
            
            if verify_hotspot_broadcasting():
                print(f"[network-manager] Hotspot '{HOTSPOT_SSID}' started after restart", flush=True)
                return True
            else:
                print(f"[network-manager] Failed to start hotspot - service started but not broadcasting", flush=True)
                # Try to get more diagnostic info
                result = subprocess.run(
                    ["iw", "dev", HOTSPOT_INTERFACE, "info"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                print(f"[network-manager] Interface info: {result.stdout}", flush=True)
                return False
    except Exception as e:
        print(f"[network-manager] Error starting hotspot: {e}", flush=True)
        import traceback
        print(f"[network-manager] Traceback: {traceback.format_exc()}", flush=True)
        return False


def create_virtual_ap_interface(interface_name="wlan0_ap", phy="phy0"):
    """
    Create a virtual AP interface for concurrent AP+STA operation.
    
    Args:
        interface_name: Name of the virtual interface to create (default: wlan0_ap)
        phy: Physical device name (default: phy0)
    
    Returns:
        bool: True if interface exists or was created successfully
    """
    try:
        # Check if interface already exists
        result = subprocess.run(
            ["ip", "link", "show", interface_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            print(f"[network-manager] Virtual AP interface {interface_name} already exists", flush=True)
            return True
        
        # Check if phy device exists
        result = subprocess.run(
            ["iw", "phy", phy, "info"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            print(f"[network-manager] Error: Physical device {phy} not found", flush=True)
            return False
        
        # Create virtual AP interface
        print(f"[network-manager] Creating virtual AP interface {interface_name} on {phy}...", flush=True)
        result = subprocess.run(
            ["iw", "phy", phy, "interface", "add", interface_name, "type", "__ap"],
            capture_output=True,
            text=True,
            check=False
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown error"
            print(f"[network-manager] Error creating virtual AP interface: {error_msg}", flush=True)
            return False
        
        # Wait a moment for interface to appear
        time.sleep(1)
        
        # Verify interface was created
        result = subprocess.run(
            ["ip", "link", "show", interface_name],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
            print(f"[network-manager] Virtual AP interface {interface_name} created successfully", flush=True)
            return True
        else:
            print(f"[network-manager] Warning: Interface {interface_name} not found after creation", flush=True)
            return False
    
    except Exception as e:
        print(f"[network-manager] Error creating virtual AP interface: {e}", flush=True)
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
    
    # Create virtual AP interface if it doesn't exist
    if HOTSPOT_INTERFACE == "wlan0_ap":
        print(f"[network-manager] Ensuring virtual AP interface {HOTSPOT_INTERFACE} exists...", flush=True)
        if not create_virtual_ap_interface(HOTSPOT_INTERFACE):
            print(f"[network-manager] Warning: Could not create virtual AP interface {HOTSPOT_INTERFACE}", flush=True)
            print("[network-manager] Will continue and retry in monitor loop", flush=True)
        else:
            print(f"[network-manager] Virtual AP interface {HOTSPOT_INTERFACE} is ready", flush=True)
    
    # Set WiFi country code if not set (required for AP mode)
    # This must be done early and persistently
    try:
        result = subprocess.run(
            ["iw", "reg", "get"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0:
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
                # Set regulatory domain (global)
                set_result = subprocess.run(
                    ["iw", "reg", "set", country_code],
                    capture_output=True,
                    text=True,
                    check=False
                )
                if set_result.returncode != 0:
                    print(f"[network-manager] Error setting country code: {set_result.stderr}", flush=True)
                else:
                    time.sleep(2)
                    # Verify it was set
                    verify_result = subprocess.run(
                        ["iw", "reg", "get"],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if verify_result.returncode == 0:
                        # Check both global and phy level
                        if country_code.lower() in verify_result.stdout.lower() and "phy#0 country 99" not in verify_result.stdout:
                            print(f"[network-manager] Regulatory domain successfully set to {country_code}", flush=True)
                            global _regulatory_domain_set
                            _regulatory_domain_set = True
                        else:
                            print(f"[network-manager] Warning: Regulatory domain may not be set correctly at phy level", flush=True)
                            print(f"[network-manager] Current reg output: {verify_result.stdout}", flush=True)
                            # Try setting it again with interface reset (sometimes needed for phy-level update)
                            print(f"[network-manager] Attempting to set regulatory domain with interface reset...", flush=True)
                            # Bring interface down temporarily to force phy-level update
                            subprocess.run(
                                ["ip", "link", "set", HOTSPOT_INTERFACE, "down"],
                                check=False,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL
                            )
                            time.sleep(1)
                            subprocess.run(
                                ["iw", "reg", "set", country_code],
                                check=False,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL
                            )
                            time.sleep(1)
                            # Interface will be brought up later in the function
                            print(f"[network-manager] Note: phy#0 may still show country 99 due to driver limitations", flush=True)
                            print(f"[network-manager] This is often a firmware/driver issue but hotspot may still work", flush=True)
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
            print("[network-manager] Warning: Hotspot config files not found. Run install script.", flush=True)
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
                # With AP+STA concurrent support, hotspot (wlan0_ap) and WiFi client (wlan0) can run simultaneously
                # No need to check if wlan0 is in managed mode - they use different interfaces
                
                # Ensure virtual AP interface exists
                if HOTSPOT_INTERFACE == "wlan0_ap":
                    result = subprocess.run(
                        ["ip", "link", "show", HOTSPOT_INTERFACE],
                        capture_output=True,
                        text=True,
                        check=False
                    )
                    if result.returncode != 0:
                        # Virtual interface doesn't exist, try to create it
                        print(f"[network-manager] Virtual AP interface {HOTSPOT_INTERFACE} not found, creating...", flush=True)
                        create_virtual_ap_interface(HOTSPOT_INTERFACE)
                
                # Try to start/restart hotspot
                if not is_hotspot_running() or not verify_hotspot_broadcasting():
                    if not is_hotspot_running():
                        # Start if not running
                        if ensure_hotspot_config():
                            start_hotspot()
                    else:
                        # Service is running but not broadcasting - restart it
                        print("[network-manager] Hotspot service running but not broadcasting, restarting...", flush=True)
                        subprocess.run(
                            ["systemctl", "restart", HOSTAPD_SERVICE],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        time.sleep(2)
                
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

