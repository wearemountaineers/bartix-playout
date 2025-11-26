#!/usr/bin/env python3
"""
Network configuration utility for Raspberry Pi.
Applies WiFi or LAN (static IP) configuration changes.
"""
import os
import sys
import argparse
import subprocess
import ipaddress
import shutil
from pathlib import Path

# Configuration paths
WPA_SUPPLICANT_CONF = "/etc/wpa_supplicant/wpa_supplicant.conf"
DHCPCD_CONF = "/etc/dhcpcd.conf"
DHCPCD_CONF_BACKUP = "/etc/dhcpcd.conf.bak"
WPA_SUPPLICANT_BACKUP = "/etc/wpa_supplicant/wpa_supplicant.conf.bak"


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


def backup_file(filepath):
    """Create backup of configuration file."""
    if os.path.exists(filepath):
        backup_path = filepath + ".bak"
        shutil.copy2(filepath, backup_path)
        return backup_path
    return None


def configure_wifi(ssid, password=None):
    """
    Configure WiFi connection by updating wpa_supplicant.conf.
    
    Args:
        ssid: WiFi network SSID
        password: WiFi password (optional for open networks)
    """
    print(f"[network-config] Configuring WiFi: {ssid}", flush=True)
    
    # IMPORTANT: Stop hotspot before configuring WiFi (wlan0 conflict)
    # The hotspot uses wlan0 in AP mode, but WiFi client needs it in managed mode
    # They cannot coexist on the same interface
    print("[network-config] Stopping hotspot to free wlan0 interface for WiFi client...", flush=True)
    subprocess.run(
        ["systemctl", "stop", "hostapd"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["systemctl", "stop", "dnsmasq"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    # Bring interface down to reset mode
    subprocess.run(
        ["ip", "link", "set", "wlan0", "down"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    import time
    time.sleep(2)
    print("[network-config] Hotspot stopped, wlan0 interface freed", flush=True)
    
    # CRITICAL: Remove denyinterfaces wlan0 from dhcpcd.conf
    # This allows dhcpcd to manage wlan0 and get an IP address via DHCP
    print("[network-config] Enabling dhcpcd management of wlan0 for WiFi client...", flush=True)
    dhcpcd_conf = "/etc/dhcpcd.conf"
    if os.path.exists(dhcpcd_conf):
        with open(dhcpcd_conf, 'r') as f:
            dhcpcd_lines = f.readlines()
        
        # Remove denyinterfaces wlan0 line
        new_dhcpcd_lines = []
        removed = False
        for line in dhcpcd_lines:
            if "denyinterfaces wlan0" not in line.strip():
                new_dhcpcd_lines.append(line)
            else:
                removed = True
        
        if removed:
            with open(dhcpcd_conf, 'w') as f:
                f.writelines(new_dhcpcd_lines)
            print("[network-config] Removed 'denyinterfaces wlan0' from dhcpcd.conf", flush=True)
        else:
            print("[network-config] 'denyinterfaces wlan0' not found in dhcpcd.conf (already removed)", flush=True)
    else:
        print("[network-config] Warning: /etc/dhcpcd.conf not found", flush=True)
    
    # Backup existing config
    backup_file(WPA_SUPPLICANT_CONF)
    
    # Read existing config or create new
    config_lines = []
    if os.path.exists(WPA_SUPPLICANT_CONF):
        with open(WPA_SUPPLICANT_CONF, 'r') as f:
            config_lines = f.readlines()
    
    # Find or create network block
    network_start = -1
    network_end = -1
    in_network = False
    brace_count = 0
    
    for i, line in enumerate(config_lines):
        stripped = line.strip()
        if stripped.startswith("network={"):
            network_start = i
            in_network = True
            brace_count = stripped.count("{") - stripped.count("}")
        elif in_network:
            brace_count += line.count("{") - line.count("}")
            if brace_count == 0:
                network_end = i + 1
                break
    
    # Create new network block
    new_network = ["network={\n"]
    new_network.append(f'    ssid="{ssid}"\n')
    if password:
        new_network.append(f'    psk="{password}"\n')
    else:
        new_network.append("    key_mgmt=NONE\n")
    new_network.append("}\n")
    
    # Replace or append network block
    if network_start >= 0 and network_end > network_start:
        # Replace existing network block
        config_lines = config_lines[:network_start] + new_network + config_lines[network_end:]
    else:
        # Append new network block
        # Ensure file ends with newline
        if config_lines and not config_lines[-1].endswith("\n"):
            config_lines[-1] += "\n"
        config_lines.extend(new_network)
    
    # Write updated config
    os.makedirs(os.path.dirname(WPA_SUPPLICANT_CONF), exist_ok=True)
    with open(WPA_SUPPLICANT_CONF, 'w') as f:
        f.writelines(config_lines)
    
    print(f"[network-config] WiFi configuration updated", flush=True)
    return True


def configure_lan_static(ip, subnet, gateway, dns="8.8.8.8", interface="eth0"):
    """
    Configure static IP for LAN interface by updating dhcpcd.conf.
    
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
    
    # Backup existing config
    backup_file(DHCPCD_CONF)
    
    # Read existing config
    config_lines = []
    if os.path.exists(DHCPCD_CONF):
        with open(DHCPCD_CONF, 'r') as f:
            config_lines = f.readlines()
    
    # Remove existing static IP configuration for this interface
    new_lines = []
    skip_until_blank = False
    for line in config_lines:
        stripped = line.strip()
        if skip_until_blank:
            if not stripped or stripped.startswith("#"):
                skip_until_blank = False
                new_lines.append(line)
            continue
        
        if stripped.startswith(f"interface {interface}"):
            skip_until_blank = True
            continue
        new_lines.append(line)
    
    # Add new static IP configuration
    new_lines.append(f"\n# Static IP configuration for {interface}\n")
    new_lines.append(f"interface {interface}\n")
    new_lines.append(f"static ip_address={ip}/{subnet}\n")
    new_lines.append(f"static routers={gateway}\n")
    new_lines.append(f"static domain_name_servers={dns}\n")
    
    # Write updated config
    with open(DHCPCD_CONF, 'w') as f:
        f.writelines(new_lines)
    
    print(f"[network-config] LAN static IP configuration updated", flush=True)
    return True


def restart_network_services():
    """Restart network services to apply configuration."""
    print("[network-config] Restarting network services...", flush=True)
    
    import time
    
    try:
        # Restart dhcpcd FIRST (handles both DHCP and static IP)
        # This is critical for WiFi client to get an IP address
        subprocess.run(
            ["systemctl", "restart", "dhcpcd"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        print("[network-config] dhcpcd restarted", flush=True)
        time.sleep(2)  # Give dhcpcd time to start
    except subprocess.CalledProcessError as e:
        print(f"[network-config] Warning: Failed to restart dhcpcd: {e}", flush=True)
        # Continue anyway
    
    try:
        # Restart wpa_supplicant AFTER dhcpcd
        subprocess.run(
            ["systemctl", "restart", "wpa_supplicant"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        print("[network-config] wpa_supplicant restarted", flush=True)
        time.sleep(3)  # Give wpa_supplicant time to connect
    except subprocess.CalledProcessError as e:
        print(f"[network-config] Warning: Failed to restart wpa_supplicant: {e}", flush=True)
        # Continue anyway
    
    # Give services time to settle
    time.sleep(2)
    
    print("[network-config] Network services restarted", flush=True)
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Configure network settings")
    parser.add_argument("--network-type", choices=["wifi", "lan"], required=True,
                       help="Network type: wifi or lan")
    
    # WiFi arguments
    parser.add_argument("--ssid", help="WiFi SSID")
    parser.add_argument("--password", help="WiFi password")
    
    # LAN arguments
    parser.add_argument("--ip", help="Static IP address")
    parser.add_argument("--subnet", help="Subnet mask")
    parser.add_argument("--gateway", help="Gateway IP address")
    parser.add_argument("--dns", default="8.8.8.8", help="DNS server (default: 8.8.8.8)")
    parser.add_argument("--interface", default="eth0", help="Network interface (default: eth0)")
    
    args = parser.parse_args()
    
    try:
        if args.network_type == "wifi":
            if not args.ssid:
                print("[network-config] Error: --ssid is required for WiFi", flush=True)
                sys.exit(1)
            configure_wifi(args.ssid, args.password)
        elif args.network_type == "lan":
            if not all([args.ip, args.subnet, args.gateway]):
                print("[network-config] Error: --ip, --subnet, and --gateway are required for LAN", flush=True)
                sys.exit(1)
            configure_lan_static(args.ip, args.subnet, args.gateway, args.dns, args.interface)
        
        # Restart network services
        restart_network_services()
        
        print("[network-config] Configuration applied successfully", flush=True)
        sys.exit(0)
    
    except Exception as e:
        print(f"[network-config] Error: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


