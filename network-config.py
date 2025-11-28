#!/usr/bin/env python3
"""
Network configuration utility for Raspberry Pi.
Applies WiFi or LAN (static IP) configuration changes using NetworkManager.
"""
import os
import sys
import argparse
import subprocess
import ipaddress
import time
import re

# NetworkManager connection name for WiFi client
WIFI_CONNECTION_NAME = "WiFi-Client"


def validate_ip(ip_str):
    """Validate IP address format."""
    try:
        ipaddress.IPv4Address(ip_str)
        return True
    except ValueError:
        return False


def validate_subnet(subnet_str):
    """Validate subnet mask format."""
    try:
        ipaddress.IPv4Address(subnet_str)
        return True
    except ValueError:
        return False


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
        print(f"[network-config] nmcli command failed: {' '.join(full_cmd)}", flush=True)
        if result.stderr:
            print(f"[network-config] Error: {result.stderr}", flush=True)
    return result


def configure_wifi(ssid, password=None):
    """
    Configure WiFi connection using NetworkManager.
    
    Args:
        ssid: WiFi network SSID
        password: WiFi password (optional for open networks)
    """
    print(f"[network-config] Configuring WiFi: {ssid}", flush=True)
    print("[network-config] WiFi client will use wlan0, hotspot continues on wlan0_ap", flush=True)
    
    # Check if connection already exists
    result = nmcli_run(["connection", "show", WIFI_CONNECTION_NAME], check=False)
    connection_exists = (result.returncode == 0)
    
    if connection_exists:
        print(f"[network-config] Updating existing WiFi connection '{WIFI_CONNECTION_NAME}'...", flush=True)
        # Update existing connection
        nmcli_run(["connection", "modify", WIFI_CONNECTION_NAME, "wifi.ssid", ssid], check=False)
        if password:
            nmcli_run(["connection", "modify", WIFI_CONNECTION_NAME, "wifi-sec.key-mgmt", "wpa-psk"], check=False)
            nmcli_run(["connection", "modify", WIFI_CONNECTION_NAME, "wifi-sec.psk", password], check=False)
        else:
            nmcli_run(["connection", "modify", WIFI_CONNECTION_NAME, "wifi-sec.key-mgmt", "none"], check=False)
    else:
        print(f"[network-config] Creating new WiFi connection '{WIFI_CONNECTION_NAME}'...", flush=True)
        # Create new connection
        cmd = [
            "connection", "add",
            "type", "wifi",
            "con-name", WIFI_CONNECTION_NAME,
            "ifname", "wlan0",
            "autoconnect", "yes",
            "wifi.ssid", ssid
        ]
        if password:
            cmd.extend(["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])
        else:
            cmd.extend(["wifi-sec.key-mgmt", "none"])
        
        result = nmcli_run(cmd)
        if result.returncode != 0:
            print(f"[network-config] Error creating WiFi connection: {result.stderr}", flush=True)
            return False
    
    print(f"[network-config] WiFi configuration updated", flush=True)
    
    # Activate the connection
    print("[network-config] Activating WiFi connection...", flush=True)
    result = nmcli_run(["connection", "up", WIFI_CONNECTION_NAME], check=False)
    
    if result.returncode != 0:
        print(f"[network-config] Warning: Failed to activate connection: {result.stderr}", flush=True)
        print("[network-config] Connection will be activated automatically when network is available", flush=True)
    
    # Monitor WiFi connection status
    print("[network-config] Monitoring WiFi connection status...", flush=True)
    max_wait = 30  # Wait up to 30 seconds for connection
    check_interval = 2  # Check every 2 seconds
    
    for i in range(0, max_wait, check_interval):
        time.sleep(check_interval)
        
        # Check if wlan0 has an IP address
        result = subprocess.run(
            ["ip", "addr", "show", "wlan0"],
            capture_output=True,
            text=True,
            check=False
        )
        
        has_ip = False
        ip_address = None
        if result.returncode == 0:
            # Look for inet address (not 127.0.0.1)
            matches = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)/', result.stdout)
            for match in matches:
                if match != "127.0.0.1":
                    has_ip = True
                    ip_address = match
                    break
        
        # Check NetworkManager connection status
        nm_status = "unknown"
        nm_ssid = ""
        result = nmcli_run(["connection", "show", "--active", WIFI_CONNECTION_NAME], check=False)
        if result.returncode == 0:
            # Parse connection info
            for line in result.stdout.splitlines():
                if "GENERAL.STATE:" in line:
                    nm_status = line.split(":", 1)[1].strip() if ":" in line else "unknown"
                elif "802-11-wireless.ssid:" in line:
                    nm_ssid = line.split(":", 1)[1].strip() if ":" in line else ""
        
        # Log detailed status every 6 seconds (every 3rd check)
        if (i // check_interval) % 3 == 0 and i > 0:
            print(f"[network-config] Detailed status - State: {nm_status}, SSID: {nm_ssid or 'none'}", flush=True)
        
        if has_ip:
            print(f"[network-config] ✓ WiFi connection successful!", flush=True)
            print(f"[network-config] IP address: {ip_address}", flush=True)
            print(f"[network-config] Connection state: {nm_status}", flush=True)
            return True
        else:
            elapsed = i + check_interval
            print(f"[network-config] Waiting for WiFi connection... ({elapsed}s/{max_wait}s, state: {nm_status})", flush=True)
    
    # Final check
    result = subprocess.run(
        ["ip", "addr", "show", "wlan0"],
        capture_output=True,
        text=True,
        check=False
    )
    has_ip = False
    ip_address = None
    if result.returncode == 0:
        matches = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)/', result.stdout)
        for match in matches:
            if match != "127.0.0.1":
                has_ip = True
                ip_address = match
                break
    
    if has_ip:
        print(f"[network-config] ✓ WiFi connection successful (after {max_wait}s)!", flush=True)
        print(f"[network-config] IP address: {ip_address}", flush=True)
        return True
    else:
        print(f"[network-config] ⚠ WiFi configuration applied but no IP address obtained after {max_wait}s", flush=True)
        print(f"[network-config] Connection state: {nm_status}", flush=True)
        
        # Get final connection details
        result = nmcli_run(["connection", "show", WIFI_CONNECTION_NAME], check=False)
        if result.returncode == 0:
            print(f"[network-config] Connection details:", flush=True)
            for line in result.stdout.splitlines()[:10]:  # First 10 lines
                if line.strip():
                    print(f"[network-config]   {line}", flush=True)
        
        print(f"[network-config] Check connection status: nmcli connection show '{WIFI_CONNECTION_NAME}'", flush=True)
        print(f"[network-config] Check device status: nmcli device status", flush=True)
        return False


def clear_wifi():
    """
    Clear WiFi credentials by deleting NetworkManager WiFi connection.
    This allows the system to fall back to hotspot mode.
    """
    print(f"[network-config] Clearing WiFi credentials...", flush=True)
    
    # Check if connection exists
    result = nmcli_run(["connection", "show", WIFI_CONNECTION_NAME], check=False)
    if result.returncode != 0:
        print(f"[network-config] No WiFi connection found to clear", flush=True)
        return True
    
    # Deactivate connection first
    print(f"[network-config] Deactivating WiFi connection...", flush=True)
    nmcli_run(["connection", "down", WIFI_CONNECTION_NAME], check=False)
    time.sleep(1)
    
    # Delete the connection
    print(f"[network-config] Deleting WiFi connection...", flush=True)
    result = nmcli_run(["connection", "delete", WIFI_CONNECTION_NAME], check=False)
    
    if result.returncode == 0:
        print(f"[network-config] WiFi credentials cleared", flush=True)
        return True
    else:
        print(f"[network-config] Warning: Failed to delete connection: {result.stderr}", flush=True)
        return False


def configure_lan_dhcp(interface="eth0"):
    """
    Configure DHCP for LAN interface using NetworkManager.
    
    Args:
        interface: Network interface name (default: eth0)
    """
    print(f"[network-config] Configuring LAN with DHCP on {interface}", flush=True)
    
    connection_name = f"Wired-{interface}"
    
    # Check if connection exists
    result = nmcli_run(["connection", "show", connection_name], check=False)
    connection_exists = (result.returncode == 0)
    
    if connection_exists:
        print(f"[network-config] Updating existing LAN connection '{connection_name}' to use DHCP...", flush=True)
        # Update existing connection to use DHCP
        nmcli_run(["connection", "modify", connection_name, "ipv4.method", "auto"], check=False)
        # Remove static IP settings if they exist
        nmcli_run(["connection", "modify", connection_name, "ipv4.addresses", ""], check=False)
        nmcli_run(["connection", "modify", connection_name, "ipv4.gateway", ""], check=False)
        nmcli_run(["connection", "modify", connection_name, "ipv4.dns", ""], check=False)
    else:
        print(f"[network-config] Creating new LAN connection '{connection_name}' with DHCP...", flush=True)
        # Create new connection with DHCP
        result = nmcli_run([
            "connection", "add",
            "type", "ethernet",
            "con-name", connection_name,
            "ifname", interface,
            "ipv4.method", "auto",
            "autoconnect", "yes"
        ])
        
        if result.returncode != 0:
            print(f"[network-config] Error creating LAN connection: {result.stderr}", flush=True)
            return False
    
    print(f"[network-config] LAN DHCP configuration updated", flush=True)
    
    # Activate the connection
    print("[network-config] Activating LAN connection...", flush=True)
    result = nmcli_run(["connection", "up", connection_name], check=False)
    
    if result.returncode != 0:
        print(f"[network-config] Warning: Failed to activate connection: {result.stderr}", flush=True)
        print("[network-config] Connection will be activated automatically when interface is available", flush=True)
    
    # Wait a moment for DHCP to obtain IP
    print("[network-config] Waiting for DHCP to obtain IP address...", flush=True)
    time.sleep(3)
    
    # Check if IP was obtained
    result = subprocess.run(
        ["ip", "addr", "show", interface],
        capture_output=True,
        text=True,
        check=False
    )
    has_ip = False
    ip_address = None
    if result.returncode == 0:
        matches = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)/', result.stdout)
        for match in matches:
            if match != "127.0.0.1":
                has_ip = True
                ip_address = match
                break
    
    if has_ip:
        print(f"[network-config] ✓ DHCP configuration successful!", flush=True)
        print(f"[network-config] IP address: {ip_address}", flush=True)
    else:
        print(f"[network-config] ⚠ DHCP configuration applied but no IP address obtained yet", flush=True)
        print(f"[network-config] DHCP will continue attempting to obtain an IP address", flush=True)
    
    return True


def configure_lan_static(ip, subnet, gateway, dns="8.8.8.8", interface="eth0"):
    """
    Configure static IP for LAN interface using NetworkManager.
    
    Args:
        ip: Static IP address
        subnet: Subnet mask
        gateway: Gateway IP address
        dns: DNS server IP (default: 8.8.8.8)
        interface: Network interface name (default: eth0)
    """
    print(f"[network-config] Configuring LAN static IP: {ip}/{subnet}", flush=True)
    
    # Validate inputs
    if not validate_ip(ip):
        raise ValueError(f"Invalid IP address: {ip}")
    if not validate_subnet(subnet):
        raise ValueError(f"Invalid subnet mask: {subnet}")
    if not validate_ip(gateway):
        raise ValueError(f"Invalid gateway: {gateway}")
    if not validate_ip(dns):
        raise ValueError(f"Invalid DNS: {dns}")
    
    # Convert subnet mask to CIDR notation
    # Simple conversion for common masks
    subnet_to_cidr = {
        "255.255.255.0": "24",
        "255.255.0.0": "16",
        "255.0.0.0": "8"
    }
    cidr = subnet_to_cidr.get(subnet, "24")  # Default to /24
    
    connection_name = f"Wired-{interface}"
    
    # Check if connection exists
    result = nmcli_run(["connection", "show", connection_name], check=False)
    connection_exists = (result.returncode == 0)
    
    if connection_exists:
        print(f"[network-config] Updating existing LAN connection '{connection_name}'...", flush=True)
        # Update existing connection
        nmcli_run(["connection", "modify", connection_name, "ipv4.method", "manual"], check=False)
        nmcli_run(["connection", "modify", connection_name, "ipv4.addresses", f"{ip}/{cidr}"], check=False)
        nmcli_run(["connection", "modify", connection_name, "ipv4.gateway", gateway], check=False)
        nmcli_run(["connection", "modify", connection_name, "ipv4.dns", dns], check=False)
    else:
        print(f"[network-config] Creating new LAN connection '{connection_name}'...", flush=True)
        # Create new connection
        result = nmcli_run([
            "connection", "add",
            "type", "ethernet",
            "con-name", connection_name,
            "ifname", interface,
            "ipv4.method", "manual",
            "ipv4.addresses", f"{ip}/{cidr}",
            "ipv4.gateway", gateway,
            "ipv4.dns", dns,
            "autoconnect", "yes"
        ])
        
        if result.returncode != 0:
            print(f"[network-config] Error creating LAN connection: {result.stderr}", flush=True)
            return False
    
    print(f"[network-config] LAN static IP configuration updated", flush=True)
    
    # Activate the connection
    print("[network-config] Activating LAN connection...", flush=True)
    result = nmcli_run(["connection", "up", connection_name], check=False)
    
    if result.returncode != 0:
        print(f"[network-config] Warning: Failed to activate connection: {result.stderr}", flush=True)
        print("[network-config] Connection will be activated automatically when interface is available", flush=True)
    
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Configure network settings using NetworkManager")
    parser.add_argument("--network-type", choices=["wifi", "lan"], required=False,
                       help="Network type: wifi or lan")
    
    # WiFi arguments
    parser.add_argument("--ssid", help="WiFi SSID")
    parser.add_argument("--password", help="WiFi password")
    parser.add_argument("--clear-wifi", action="store_true",
                       help="Clear WiFi credentials (delete NetworkManager connection)")
    
    # LAN arguments
    parser.add_argument("--dhcp", action="store_true", help="Use DHCP for LAN (instead of static IP)")
    parser.add_argument("--ip", help="Static IP address (required if not using --dhcp)")
    parser.add_argument("--subnet", help="Subnet mask (required if not using --dhcp)")
    parser.add_argument("--gateway", help="Gateway IP address (required if not using --dhcp)")
    parser.add_argument("--dns", default="8.8.8.8", help="DNS server (default: 8.8.8.8, only used with static IP)")
    parser.add_argument("--interface", default="eth0", help="Network interface (default: eth0)")
    
    args = parser.parse_args()
    
    try:
        if args.clear_wifi:
            # Clear WiFi credentials
            clear_wifi()
            print("[network-config] WiFi credentials cleared successfully", flush=True)
            sys.exit(0)
        elif args.network_type == "wifi":
            if not args.ssid:
                print("[network-config] Error: --ssid is required for WiFi", flush=True)
                sys.exit(1)
            configure_wifi(args.ssid, args.password)
            print("[network-config] Configuration applied successfully", flush=True)
            sys.exit(0)
        elif args.network_type == "lan":
            if args.dhcp:
                # Configure LAN with DHCP
                configure_lan_dhcp(args.interface)
                print("[network-config] Configuration applied successfully", flush=True)
                sys.exit(0)
            else:
                # Configure LAN with static IP
                if not all([args.ip, args.subnet, args.gateway]):
                    print("[network-config] Error: --ip, --subnet, and --gateway are required for LAN static IP, or use --dhcp", flush=True)
                    sys.exit(1)
                configure_lan_static(args.ip, args.subnet, args.gateway, args.dns, args.interface)
                print("[network-config] Configuration applied successfully", flush=True)
                sys.exit(0)
        else:
            print("[network-config] Error: --network-type or --clear-wifi is required", flush=True)
            sys.exit(1)
    
    except Exception as e:
        print(f"[network-config] Error: {e}", flush=True)
        import traceback
        print(f"[network-config] Traceback: {traceback.format_exc()}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
