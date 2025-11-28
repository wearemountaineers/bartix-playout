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
    
    # With AP+STA concurrent support, hotspot (wlan0_ap) and WiFi client (wlan0) can run simultaneously
    # No need to stop hotspot - they use different interfaces
    print("[network-config] WiFi client will use wlan0, hotspot continues on wlan0_ap", flush=True)
    
    # CRITICAL: Ensure denyinterfaces wlan0 is NOT in dhcpcd.conf
    # This allows dhcpcd to manage wlan0 and get an IP address via DHCP for STA mode
    print("[network-config] Ensuring dhcpcd can manage wlan0 for WiFi client...", flush=True)
    dhcpcd_conf = "/etc/dhcpcd.conf"
    if os.path.exists(dhcpcd_conf):
        with open(dhcpcd_conf, 'r') as f:
            dhcpcd_lines = f.readlines()
        
        # Remove denyinterfaces wlan0 line (if present)
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
        print("[network-config] /etc/dhcpcd.conf not found, creating it...", flush=True)
        os.makedirs(os.path.dirname(dhcpcd_conf), exist_ok=True)
        with open(dhcpcd_conf, 'w') as f:
            f.write("# dhcpcd configuration\n")
            f.write("# Managed by network-config.py\n\n")
        print("[network-config] Created /etc/dhcpcd.conf", flush=True)
    
    # Backup existing config
    backup_file(WPA_SUPPLICANT_CONF)
    
    # Read existing config or create new
    config_lines = []
    has_header = False
    if os.path.exists(WPA_SUPPLICANT_CONF):
        with open(WPA_SUPPLICANT_CONF, 'r') as f:
            config_lines = f.readlines()
        # Check if header exists (ctrl_interface, country, update_config)
        for line in config_lines:
            if line.strip().startswith("ctrl_interface") or line.strip().startswith("country=") or line.strip().startswith("update_config"):
                has_header = True
                break
    
    # If no header, create one at the beginning
    if not has_header or not config_lines:
        header = [
            "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n",
            "update_config=1\n",
            "country=NL\n",
            "\n"
        ]
        if config_lines:
            config_lines = header + config_lines
        else:
            config_lines = header
        print("[network-config] Added required header to wpa_supplicant.conf", flush=True)
    
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
    # Explicitly set scan_ssid=1 to help with connection
    new_network.append("    scan_ssid=1\n")
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
    
    # Enable wpa_supplicant to start on boot (so WiFi auto-connects after reboot)
    print("[network-config] Enabling wpa_supplicant to start on boot...", flush=True)
    subprocess.run(
        ["systemctl", "enable", "wpa_supplicant"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )
    print("[network-config] wpa_supplicant enabled for auto-start on boot", flush=True)
    
    # Restart network services to apply configuration
    restart_network_services()
    
    import time
    
    # Check if wpa_supplicant is running
    print("[network-config] Checking wpa_supplicant status...", flush=True)
    result = subprocess.run(
        ["systemctl", "is-active", "wpa_supplicant"],
        capture_output=True,
        text=True,
        check=False
    )
    if result.returncode != 0:
        print("[network-config] Warning: wpa_supplicant is not running, attempting to start...", flush=True)
        subprocess.run(
            ["systemctl", "start", "wpa_supplicant"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )
        time.sleep(2)
    
    # Check wpa_supplicant status immediately
    result = subprocess.run(
        ["wpa_cli", "-i", "wlan0", "status"],
        capture_output=True,
        text=True,
        check=False,
        timeout=2
    )
    if result.returncode == 0:
        print(f"[network-config] Initial wpa_supplicant status:", flush=True)
        for line in result.stdout.splitlines():
            if line.strip():
                print(f"[network-config]   {line}", flush=True)
    else:
        print(f"[network-config] Warning: Could not get wpa_supplicant status: {result.stderr}", flush=True)
    
    # Monitor WiFi connection status
    print("[network-config] Monitoring WiFi connection status...", flush=True)
    import time
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
            import re
            # Look for inet address (not 127.0.0.1)
            matches = re.findall(r'inet (\d+\.\d+\.\d+\.\d+)/', result.stdout)
            for match in matches:
                if match != "127.0.0.1":
                    has_ip = True
                    ip_address = match
                    break
        
        # Check wpa_supplicant status
        wpa_status = "unknown"
        wpa_ssid = ""
        wpa_bssid = ""
        result = subprocess.run(
            ["wpa_cli", "-i", "wlan0", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("wpa_state="):
                    wpa_status = line.split("=", 1)[1].strip()
                elif line.startswith("ssid="):
                    wpa_ssid = line.split("=", 1)[1].strip()
                elif line.startswith("bssid="):
                    wpa_bssid = line.split("=", 1)[1].strip()
        
        # Log detailed status every 6 seconds (every 3rd check)
        if (i // check_interval) % 3 == 0 and i > 0:
            print(f"[network-config] Detailed status - State: {wpa_status}, SSID: {wpa_ssid or 'none'}, BSSID: {wpa_bssid or 'none'}", flush=True)
        
        if has_ip:
            print(f"[network-config] ✓ WiFi connection successful!", flush=True)
            print(f"[network-config] IP address: {ip_address}", flush=True)
            print(f"[network-config] Connection state: {wpa_status}", flush=True)
            return True
        else:
            elapsed = i + check_interval
            print(f"[network-config] Waiting for WiFi connection... ({elapsed}s/{max_wait}s, state: {wpa_status})", flush=True)
    
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
        import re
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
        print(f"[network-config] Connection state: {wpa_status}", flush=True)
        
        # Get final detailed status
        result = subprocess.run(
            ["wpa_cli", "-i", "wlan0", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2
        )
        if result.returncode == 0:
            print(f"[network-config] Final wpa_supplicant status:", flush=True)
            for line in result.stdout.splitlines():
                if line.strip():
                    print(f"[network-config]   {line}", flush=True)
        
        # Check wpa_supplicant logs for errors
        result = subprocess.run(
            ["journalctl", "-u", "wpa_supplicant", "-n", "20", "--no-pager"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"[network-config] Recent wpa_supplicant logs:", flush=True)
            for line in result.stdout.splitlines()[-10:]:  # Last 10 lines
                if line.strip():
                    print(f"[network-config]   {line}", flush=True)
        
        print(f"[network-config] Check connection status: wpa_cli -i wlan0 status", flush=True)
        print(f"[network-config] Check wpa_supplicant logs: journalctl -u wpa_supplicant -f", flush=True)
        return False


def clear_wifi():
    """
    Clear WiFi credentials by removing all network blocks from wpa_supplicant.conf.
    This allows the system to fall back to hotspot mode.
    """
    print(f"[network-config] Clearing WiFi credentials...", flush=True)
    
    # Backup existing config
    backup_file(WPA_SUPPLICANT_CONF)
    
    # Read existing config
    if not os.path.exists(WPA_SUPPLICANT_CONF):
        print(f"[network-config] No WiFi configuration found to clear", flush=True)
        return True
    
    with open(WPA_SUPPLICANT_CONF, 'r') as f:
        config_lines = f.readlines()
    
    # Remove all network blocks
    new_lines = []
    in_network = False
    brace_count = 0
    
    for line in config_lines:
        stripped = line.strip()
        if stripped.startswith("network={"):
            in_network = True
            brace_count = stripped.count("{") - stripped.count("}")
            continue  # Skip the network={ line
        elif in_network:
            brace_count += line.count("{") - line.count("}")
            if brace_count == 0:
                # End of network block
                in_network = False
            continue  # Skip all lines inside network block
        
        # Keep all non-network lines (country, ctrl_interface, etc.)
        new_lines.append(line)
    
    # Write updated config (without network blocks)
    os.makedirs(os.path.dirname(WPA_SUPPLICANT_CONF), exist_ok=True)
    with open(WPA_SUPPLICANT_CONF, 'w') as f:
        f.writelines(new_lines)
    
    print(f"[network-config] WiFi credentials cleared", flush=True)
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
        # Check if dhcpcd service exists
        result = subprocess.run(
            ["systemctl", "list-unit-files", "dhcpcd.service"],
            capture_output=True,
            text=True,
            check=False
        )
        if "dhcpcd.service" in result.stdout:
            # Restart dhcpcd FIRST (handles both DHCP and static IP)
            # This is critical for WiFi client to get an IP address
            print("[network-config] Restarting dhcpcd...", flush=True)
            subprocess.run(
                ["systemctl", "restart", "dhcpcd"],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE
            )
            print("[network-config] dhcpcd restarted", flush=True)
            time.sleep(2)  # Give dhcpcd time to start
        else:
            print("[network-config] Warning: dhcpcd service not found, skipping restart", flush=True)
            print("[network-config] Note: Network configuration may still work if using NetworkManager or other DHCP client", flush=True)
    except subprocess.CalledProcessError as e:
        print(f"[network-config] Warning: Failed to restart dhcpcd: {e}", flush=True)
        # Continue anyway - dhcpcd might not be installed or might be managed differently
    
    try:
        # Ensure wpa_supplicant systemd override exists to use wlan0 only
        override_dir = "/etc/systemd/system/wpa_supplicant.service.d"
        override_file = f"{override_dir}/override.conf"
        if not os.path.exists(override_file):
            print("[network-config] Creating wpa_supplicant systemd override to use wlan0 only...", flush=True)
            os.makedirs(override_dir, exist_ok=True)
            with open(override_file, 'w') as f:
                f.write("[Service]\n")
                f.write("# Force wpa_supplicant to use wlan0 only (not wlan0_ap)\n")
                f.write("# This is critical for AP+STA concurrent operation\n")
                f.write("ExecStart=\n")
                f.write("ExecStart=/sbin/wpa_supplicant -u -s -O /run/wpa_supplicant -i wlan0 -c /etc/wpa_supplicant/wpa_supplicant.conf\n")
            subprocess.run(
                ["systemctl", "daemon-reload"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            print("[network-config] wpa_supplicant override created", flush=True)
        
        # Restart wpa_supplicant AFTER dhcpcd
        print("[network-config] Restarting wpa_supplicant...", flush=True)
        result = subprocess.run(
            ["systemctl", "restart", "wpa_supplicant"],
            check=True,
            capture_output=True,
            text=True
        )
        print("[network-config] wpa_supplicant restarted", flush=True)
        
        # Check if wpa_supplicant started successfully
        time.sleep(2)
        status_result = subprocess.run(
            ["systemctl", "is-active", "wpa_supplicant"],
            capture_output=True,
            text=True,
            check=False
        )
        if status_result.returncode == 0:
            print("[network-config] wpa_supplicant is active", flush=True)
        else:
            print("[network-config] Warning: wpa_supplicant may not be running", flush=True)
            # Check for errors
            error_result = subprocess.run(
                ["systemctl", "status", "wpa_supplicant", "-n", "10", "--no-pager"],
                capture_output=True,
                text=True,
                check=False
            )
            if error_result.returncode == 0:
                print(f"[network-config] wpa_supplicant status:", flush=True)
                for line in error_result.stdout.splitlines()[-5:]:
                    if line.strip():
                        print(f"[network-config]   {line}", flush=True)
        
        time.sleep(1)  # Give wpa_supplicant a bit more time to initialize
    except subprocess.CalledProcessError as e:
        print(f"[network-config] Warning: Failed to restart wpa_supplicant: {e}", flush=True)
        if hasattr(e, 'stderr') and e.stderr:
            print(f"[network-config] Error details: {e.stderr}", flush=True)
        # Continue anyway
    
    # Give services time to settle
    time.sleep(2)
    
    print("[network-config] Network services restarted", flush=True)
    return True


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Configure network settings")
    parser.add_argument("--network-type", choices=["wifi", "lan"], required=False,
                       help="Network type: wifi or lan")
    
    # WiFi arguments
    parser.add_argument("--ssid", help="WiFi SSID")
    parser.add_argument("--password", help="WiFi password")
    parser.add_argument("--clear-wifi", action="store_true",
                       help="Clear WiFi credentials (remove all network blocks)")
    
    # LAN arguments
    parser.add_argument("--ip", help="Static IP address")
    parser.add_argument("--subnet", help="Subnet mask")
    parser.add_argument("--gateway", help="Gateway IP address")
    parser.add_argument("--dns", default="8.8.8.8", help="DNS server (default: 8.8.8.8)")
    parser.add_argument("--interface", default="eth0", help="Network interface (default: eth0)")
    
    args = parser.parse_args()
    
    try:
        if args.clear_wifi:
            # Clear WiFi credentials
            clear_wifi()
            # Restart network services to apply
            restart_network_services()
            print("[network-config] WiFi credentials cleared successfully", flush=True)
            sys.exit(0)
        elif args.network_type == "wifi":
            if not args.ssid:
                print("[network-config] Error: --ssid is required for WiFi", flush=True)
                sys.exit(1)
            configure_wifi(args.ssid, args.password)
            # Restart network services
            restart_network_services()
            print("[network-config] Configuration applied successfully", flush=True)
            sys.exit(0)
        elif args.network_type == "lan":
            if not all([args.ip, args.subnet, args.gateway]):
                print("[network-config] Error: --ip, --subnet, and --gateway are required for LAN", flush=True)
                sys.exit(1)
            configure_lan_static(args.ip, args.subnet, args.gateway, args.dns, args.interface)
            # Restart network services
            restart_network_services()
            print("[network-config] Configuration applied successfully", flush=True)
            sys.exit(0)
        else:
            print("[network-config] Error: --network-type or --clear-wifi is required", flush=True)
            sys.exit(1)
    
    except Exception as e:
        print(f"[network-config] Error: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()


