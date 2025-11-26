#!/usr/bin/env python3
"""
Simple HTTP server for network configuration via web interface.
Serves configuration form and handles network configuration requests.
"""
import os
import sys
import json
import http.server
import socketserver
import urllib.parse
import urllib.request
import urllib.error
import subprocess
import re
import time
import tempfile
from pathlib import Path

# Configuration
CONFIG_SERVER_PORT = int(os.environ.get("CONFIG_SERVER_PORT", "8080"))
CONFIG_HTML_PATH = os.environ.get(
    "CONFIG_HTML_PATH",
    "/usr/local/share/bartix/templates/config.html"
)
NETWORK_CONFIG_SCRIPT = os.environ.get(
    "NETWORK_CONFIG_SCRIPT",
    "/usr/local/bin/network-config.py"
)
WEB_PASSWORD_FILE = "/etc/bartix/web_password.txt"


class ConfigHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for configuration server."""
    
    def check_auth(self):
        """Check if request is authenticated."""
        # Check if password file exists
        if not os.path.exists(WEB_PASSWORD_FILE):
            return True  # No password set, allow access
        
        auth_header = self.headers.get('Authorization', '')
        if not auth_header.startswith('Basic '):
            return False
        
        import base64
        try:
            encoded = auth_header.split(' ', 1)[1]
            decoded = base64.b64decode(encoded).decode('utf-8')
            username, password = decoded.split(':', 1)
            
            # Read stored password
            with open(WEB_PASSWORD_FILE, 'r') as f:
                stored_password = f.read().strip()
            
            return password == stored_password
        except Exception:
            return False
    
    def require_auth(self):
        """Send 401 authentication required."""
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Bartix Configuration"')
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b'Authentication required')
    
    def do_GET(self):
        """Handle GET requests."""
        # Check authentication (except for login check endpoint)
        parsed_path = urllib.parse.urlparse(self.path)
        if parsed_path.path != "/check-auth" and not self.check_auth():
            self.require_auth()
            return
        
        query_params = urllib.parse.parse_qs(parsed_path.query)
        
        if parsed_path.path == "/":
            self.serve_config_form()
        elif parsed_path.path == "/status":
            self.serve_status()
        elif parsed_path.path == "/logs":
            service = query_params.get('service', ['network-manager'])[0]
            lines = int(query_params.get('lines', ['100'])[0])
            self.serve_logs(service, lines)
        elif parsed_path.path == "/check-manifest":
            url = query_params.get('url', [''])[0]
            self.check_manifest(url)
        elif parsed_path.path == "/scan-wifi":
            self.scan_wifi_networks()
        elif parsed_path.path == "/clear-wifi":
            self.clear_wifi_credentials()
        elif parsed_path.path == "/test-wifi":
            ssid = query_params.get('ssid', [''])[0]
            password = query_params.get('password', [''])[0]
            self.test_wifi_connection(ssid, password)
        elif parsed_path.path == "/check-auth":
            # Endpoint to check if password is set
            has_password = os.path.exists(WEB_PASSWORD_FILE)
            self.send_json_response(200, {"has_password": has_password})
        else:
            self.send_error(404, "Not Found")
    
    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urllib.parse.urlparse(self.path)
        
        # Check authentication (except for set-password endpoint)
        if parsed_path.path != "/set-password" and not self.check_auth():
            self.require_auth()
            return
        
        if parsed_path.path == "/configure":
            self.handle_configure()
        elif parsed_path.path == "/update-manifest":
            self.handle_update_manifest()
        elif parsed_path.path == "/set-volume":
            self.handle_set_volume()
        elif parsed_path.path == "/reboot":
            self.handle_reboot()
        elif parsed_path.path == "/update-hotspot":
            self.handle_update_hotspot()
        elif parsed_path.path == "/set-password":
            self.handle_set_password()
        elif parsed_path.path == "/clear-wifi":
            self.clear_wifi_credentials()
        else:
            self.send_error(404, "Not Found")
    
    def serve_config_form(self):
        """Serve the configuration HTML form."""
        try:
            # Try to read from installed location first, then fallback to local
            html_paths = [
                CONFIG_HTML_PATH,
                os.path.join(os.path.dirname(__file__), "templates", "config.html"),
                "templates/config.html"
            ]
            
            html_content = None
            for path in html_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        html_content = f.read()
                    break
            
            if html_content is None:
                self.send_error(500, "Configuration form not found")
                return
            
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode('utf-8'))
        except Exception as e:
            print(f"[config-server] Error serving form: {e}", flush=True)
            self.send_error(500, f"Server error: {e}")
    
    def serve_status(self):
        """Serve current network status as JSON."""
        try:
            # Import network manager functions
            sys.path.insert(0, os.path.dirname(__file__))
            try:
                # Import using importlib to handle hyphenated module name
                import importlib.util
                network_manager_path = os.path.join(os.path.dirname(__file__), "network-manager.py")
                if os.path.exists(network_manager_path):
                    spec = importlib.util.spec_from_file_location("network_manager", network_manager_path)
                    network_manager = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(network_manager)
                    has_ip, has_internet = network_manager.has_network_connectivity()
                    hotspot_running = network_manager.is_hotspot_running()
                else:
                    raise ImportError("network-manager.py not found")
            except Exception:
                # Fallback if network-manager not available
                has_ip = False
                has_internet = False
                hotspot_running = False
            
            # Get manifest URL from stream-player service
            manifest_url = ""
            try:
                result = subprocess.run(
                    ["systemctl", "show", "stream-player.service", "--property=Environment"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                for line in result.stdout.splitlines():
                    if "STREAM_MANIFEST_URL=" in line:
                        # Extract just the URL part (before any space or next env var)
                        env_line = line.split("STREAM_MANIFEST_URL=", 1)[1]
                        # Take only the URL part (until space or quote)
                        manifest_url = env_line.split()[0].strip('"').strip("'")
                        # Remove any trailing environment variable names
                        manifest_url = manifest_url.split()[0] if manifest_url else ""
                        break
            except Exception:
                pass
            
            # Get current volume (try to read from amixer)
            volume = None
            try:
                result = subprocess.run(
                    ["amixer", "get", "PCM"],
                    capture_output=True,
                    text=True,
                    check=False
                )
                # Parse volume from amixer output
                for line in result.stdout.splitlines():
                    if "%" in line and "[" in line:
                        match = re.search(r'\[(\d+)%\]', line)
                        if match:
                            volume = int(match.group(1))
                            break
            except Exception:
                pass
            
            # Get current hotspot SSID from hostapd config
            hotspot_ssid = ""
            try:
                if os.path.exists("/etc/hostapd/hostapd.conf"):
                    with open("/etc/hostapd/hostapd.conf", 'r') as f:
                        for line in f:
                            if line.strip().startswith("ssid="):
                                hotspot_ssid = line.split("=", 1)[1].strip()
                                break
            except Exception:
                pass
            
            status = {
                "has_ip": has_ip,
                "has_internet": has_internet,
                "hotspot_running": hotspot_running,
                "network_type": "unknown",
                "manifest_url": manifest_url,
                "volume": volume,
                "hotspot_ssid": hotspot_ssid
            }
            
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(status).encode('utf-8'))
        except Exception as e:
            print(f"[config-server] Error getting status: {e}", flush=True)
            self.send_error(500, f"Server error: {e}")
    
    def handle_configure(self):
        """Handle network configuration POST request."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            # Validate required fields
            network_type = data.get('network_type')
            if not network_type:
                self.send_json_response(400, {"error": "Network type is required"})
                return
            
            if network_type == "wifi":
                ssid = data.get('wifi_ssid')
                if not ssid:
                    self.send_json_response(400, {"error": "WiFi SSID is required"})
                    return
                password = data.get('wifi_password', '')
            elif network_type == "lan":
                # Validate LAN fields
                ip = data.get('lan_ip')
                subnet = data.get('lan_subnet')
                gateway = data.get('lan_gateway')
                dns = data.get('lan_dns', '8.8.8.8')
                
                if not all([ip, subnet, gateway]):
                    self.send_json_response(400, {"error": "IP, subnet, and gateway are required for LAN"})
                    return
            else:
                self.send_json_response(400, {"error": "Invalid network type"})
                return
            
            # Call network-config.py to apply configuration
            if not os.path.exists(NETWORK_CONFIG_SCRIPT):
                # Try local path
                local_script = os.path.join(os.path.dirname(__file__), "network-config.py")
                if os.path.exists(local_script):
                    script_path = local_script
                else:
                    self.send_json_response(500, {"error": "Network configuration script not found"})
                    return
            else:
                script_path = NETWORK_CONFIG_SCRIPT
            
            # Build command
            cmd = ["python3", script_path, "--network-type", network_type]
            if network_type == "wifi":
                cmd.extend(["--ssid", ssid])
                if password:
                    cmd.extend(["--password", password])
            else:  # lan
                cmd.extend([
                    "--ip", ip,
                    "--subnet", subnet,
                    "--gateway", gateway
                ])
                if dns:
                    cmd.extend(["--dns", dns])
            
            # Run configuration script
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                self.send_json_response(200, {
                    "message": "Configuration applied successfully. Network services will restart."
                })
            else:
                error_msg = result.stderr or result.stdout or "Unknown error"
                self.send_json_response(500, {
                    "error": f"Failed to apply configuration: {error_msg}"
                })
        
        except subprocess.TimeoutExpired:
            self.send_json_response(500, {"error": "Configuration timeout"})
        except json.JSONDecodeError:
            self.send_json_response(400, {"error": "Invalid JSON"})
        except Exception as e:
            print(f"[config-server] Error handling configure: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def serve_logs(self, service, lines=100):
        """Serve service logs."""
        try:
            valid_services = ['network-manager', 'config-server', 'stream-player']
            if service not in valid_services:
                self.send_json_response(400, {"error": f"Invalid service. Must be one of: {', '.join(valid_services)}"})
                return
            
            result = subprocess.run(
                ["journalctl", "-u", f"{service}.service", "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            if result.returncode == 0:
                self.send_json_response(200, {"logs": result.stdout})
            else:
                self.send_json_response(500, {"error": result.stderr or "Failed to get logs"})
        except subprocess.TimeoutExpired:
            self.send_json_response(500, {"error": "Timeout getting logs"})
        except Exception as e:
            print(f"[config-server] Error getting logs: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def check_manifest(self, url):
        """Check if manifest URL is accessible."""
        if not url:
            self.send_json_response(400, {"error": "URL is required"})
            return
        
        # Clean up URL - remove any extra whitespace or environment variables
        url = url.strip()
        # Remove any trailing environment variable names (they might be appended)
        url = url.split()[0] if url else ""
        
        # Basic URL validation
        if not url.startswith(('http://', 'https://')):
            self.send_json_response(400, {"error": "URL must start with http:// or https://"})
            return
        
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bartix-config/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
                stream_url = data.get('stream_url', '')
                self.send_json_response(200, {
                    "accessible": True,
                    "stream_url": stream_url
                })
        except urllib.error.HTTPError as e:
            self.send_json_response(200, {
                "accessible": False,
                "error": f"HTTP {e.code}: {e.reason}"
            })
        except urllib.error.URLError as e:
            self.send_json_response(200, {
                "accessible": False,
                "error": f"URL Error: {str(e)}"
            })
        except json.JSONDecodeError:
            self.send_json_response(200, {
                "accessible": False,
                "error": "Response is not valid JSON"
            })
        except Exception as e:
            self.send_json_response(200, {
                "accessible": False,
                "error": str(e)
            })
    
    def handle_update_manifest(self):
        """Handle manifest URL update."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            url = data.get('url')
            if not url:
                self.send_json_response(400, {"error": "URL is required"})
                return
            
            # Update systemd service environment variable
            service_file = "/etc/systemd/system/stream-player.service"
            if not os.path.exists(service_file):
                self.send_json_response(500, {"error": "Service file not found"})
                return
            
            # Read current service file
            with open(service_file, 'r') as f:
                content = f.read()
            
            # Update STREAM_MANIFEST_URL
            pattern = r'Environment=STREAM_MANIFEST_URL=.*'
            replacement = f'Environment=STREAM_MANIFEST_URL={url}'
            
            if re.search(pattern, content):
                content = re.sub(pattern, replacement, content)
            else:
                # Add it if it doesn't exist
                content = content.replace(
                    '[Service]',
                    f'[Service]\n{replacement}'
                )
            
            # Write back
            with open(service_file, 'w') as f:
                f.write(content)
            
            # Reload systemd and restart service
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "restart", "stream-player.service"], check=False)
            
            self.send_json_response(200, {
                "message": "Manifest URL updated successfully. Service restarted."
            })
        except Exception as e:
            print(f"[config-server] Error updating manifest: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def handle_set_volume(self):
        """Handle volume setting."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            volume = data.get('volume')
            if volume is None:
                self.send_json_response(400, {"error": "Volume is required"})
                return
            
            volume = max(0, min(100, int(volume)))
            
            # Set volume using amixer
            for ctl in ("PCM", "Headphone", "Speaker"):
                result = subprocess.run(
                    ["amixer", "set", ctl, f"{volume}%"],
                    capture_output=True,
                    text=True,
                    check=False
                )
            
            self.send_json_response(200, {
                "message": f"Volume set to {volume}%"
            })
        except Exception as e:
            print(f"[config-server] Error setting volume: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def handle_update_hotspot(self):
        """Handle hotspot SSID and password update."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            ssid = data.get('ssid', '').strip()
            password = data.get('password', '').strip()
            
            if not ssid:
                self.send_json_response(400, {"error": "SSID is required"})
                return
            
            if password and len(password) < 8:
                self.send_json_response(400, {"error": "Password must be at least 8 characters"})
                return
            
            # Read current hostapd config
            hostapd_conf = "/etc/hostapd/hostapd.conf"
            if not os.path.exists(hostapd_conf):
                self.send_json_response(500, {"error": "hostapd config file not found"})
                return
            
            with open(hostapd_conf, 'r') as f:
                content = f.read()
            
            # Update SSID
            content = re.sub(r'^ssid=.*$', f'ssid={ssid}', content, flags=re.MULTILINE)
            
            # Update password if provided
            if password:
                content = re.sub(r'^wpa_passphrase=.*$', f'wpa_passphrase={password}', content, flags=re.MULTILINE)
                # Ensure WPA is enabled
                if 'wpa=2' not in content:
                    content = re.sub(r'^wpa=.*$', 'wpa=2', content, flags=re.MULTILINE)
            else:
                # If no password provided, keep existing password (don't change it)
                pass
            
            # Write updated config
            with open(hostapd_conf, 'w') as f:
                f.write(content)
            
            # Update systemd service environment variables
            service_file = "/etc/systemd/system/network-manager.service"
            if os.path.exists(service_file):
                with open(service_file, 'r') as f:
                    service_content = f.read()
                
                # Update HOTSPOT_SSID
                service_content = re.sub(
                    r'Environment=HOTSPOT_SSID=.*',
                    f'Environment=HOTSPOT_SSID={ssid}',
                    service_content
                )
                
                # Update HOTSPOT_PASSWORD if password provided
                if password:
                    service_content = re.sub(
                        r'Environment=HOTSPOT_PASSWORD=.*',
                        f'Environment=HOTSPOT_PASSWORD={password}',
                        service_content
                    )
                
                with open(service_file, 'w') as f:
                    f.write(service_content)
                
                # Reload systemd
                subprocess.run(["systemctl", "daemon-reload"], check=True)
            
            # Restart hostapd to apply changes
            subprocess.run(["systemctl", "restart", "hostapd"], check=False)
            
            # Restart network-manager to pick up new environment variables
            subprocess.run(["systemctl", "restart", "network-manager.service"], check=False)
            
            self.send_json_response(200, {
                "message": f"Hotspot settings updated. SSID: {ssid}"
            })
        except Exception as e:
            print(f"[config-server] Error updating hotspot: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def scan_wifi_networks(self):
        """Scan for available WiFi networks."""
        try:
            # Temporarily stop hostapd if running (needed to scan)
            hostapd_running = False
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", "--quiet", "hostapd"],
                    check=False
                )
                if result.returncode == 0:
                    hostapd_running = True
                    print("[config-server] Stopping hostapd temporarily for scan...", flush=True)
                    subprocess.run(
                        ["systemctl", "stop", "hostapd"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    time.sleep(2)
            except Exception:
                pass
            
            # Unblock WiFi
            subprocess.run(
                ["rfkill", "unblock", "wifi"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)
            
            # Bring interface up if down
            subprocess.run(
                ["ip", "link", "set", "wlan0", "up"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)
            
            # Scan for networks using iw (preferred)
            result = subprocess.run(
                ["iw", "dev", "wlan0", "scan"],
                capture_output=True,
                text=True,
                timeout=20
            )
            
            networks = []
            scan_success = False
            
            if result.returncode == 0:
                # Parse iw scan output
                current_network = {}
                for line in result.stdout.splitlines():
                    line = line.strip()
                    if line.startswith("BSS "):
                        if current_network and "ssid" in current_network:
                            networks.append(current_network)
                        current_network = {}
                    elif "SSID: " in line:
                        ssid = line.split("SSID: ", 1)[1].strip()
                        if ssid:
                            current_network["ssid"] = ssid
                    elif "signal: " in line:
                        try:
                            signal_str = line.split("signal: ", 1)[1].strip().split()[0]
                            current_network["signal"] = int(float(signal_str))
                        except:
                            pass
                    elif "freq: " in line:
                        freq = line.split("freq: ", 1)[1].strip().split()[0]
                        current_network["frequency"] = freq
                
                if current_network and "ssid" in current_network:
                    networks.append(current_network)
                scan_success = True
            
            # Try iwlist if iw failed or found no networks
            if not scan_success or not networks:
                result = subprocess.run(
                    ["iwlist", "wlan0", "scan"],
                    capture_output=True,
                    text=True,
                    timeout=20
                )
                
                if result.returncode == 0:
                    current_network = {}
                    for line in result.stdout.splitlines():
                        line = line.strip()
                        if "Cell " in line and "Address:" in line:
                            if current_network and "ssid" in current_network:
                                networks.append(current_network)
                            current_network = {}
                        elif "ESSID:" in line:
                            ssid = line.split("ESSID:", 1)[1].strip().strip('"')
                            if ssid:
                                current_network["ssid"] = ssid
                        elif "Signal level=" in line:
                            try:
                                signal_str = line.split("Signal level=", 1)[1].strip().split()[0]
                                # iwlist uses negative dBm, convert if needed
                                signal = int(signal_str)
                                if signal > 0:  # If positive, it's probably in different format
                                    signal = -signal
                                current_network["signal"] = signal
                            except:
                                pass
                    
                    if current_network and "ssid" in current_network:
                        networks.append(current_network)
                    scan_success = True
            
            # Restart hostapd if it was running
            if hostapd_running:
                time.sleep(1)
                print("[config-server] Restarting hostapd after scan...", flush=True)
                subprocess.run(
                    ["systemctl", "start", "hostapd"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            
            if scan_success:
                # Remove duplicates and sort by signal strength
                seen = set()
                unique_networks = []
                for net in networks:
                    ssid = net.get("ssid", "")
                    if ssid and ssid not in seen and ssid != "":
                        seen.add(ssid)
                        unique_networks.append(net)
                
                # Sort by signal strength (higher is better)
                unique_networks.sort(key=lambda x: x.get("signal", -100), reverse=True)
                
                self.send_json_response(200, {"networks": unique_networks})
            else:
                self.send_json_response(500, {
                    "error": f"Failed to scan: {result.stderr or 'Unknown error'}"
                })
        except subprocess.TimeoutExpired:
            self.send_json_response(500, {"error": "Scan timeout"})
        except Exception as e:
            print(f"[config-server] Error scanning WiFi: {e}", flush=True)
            import traceback
            print(f"[config-server] Traceback: {traceback.format_exc()}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def test_wifi_connection(self, ssid, password):
        """Test WiFi connection without applying configuration."""
        if not ssid:
            self.send_json_response(400, {"error": "SSID is required"})
            return
        
        try:
            # Temporarily stop hostapd if running
            hostapd_running = False
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", "--quiet", "hostapd"],
                    check=False
                )
                if result.returncode == 0:
                    hostapd_running = True
                    subprocess.run(
                        ["systemctl", "stop", "hostapd"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    time.sleep(2)
            except Exception:
                pass
            
            # Create temporary wpa_supplicant config
            import tempfile
            temp_conf = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.conf')
            temp_conf.write("country=NL\n")
            temp_conf.write("ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\n")
            temp_conf.write("update_config=1\n\n")
            temp_conf.write("network={\n")
            temp_conf.write(f'    ssid="{ssid}"\n')
            if password:
                temp_conf.write(f'    psk="{password}"\n')
            else:
                temp_conf.write("    key_mgmt=NONE\n")
            temp_conf.write("}\n")
            temp_conf.close()
            
            # Stop existing wpa_supplicant
            subprocess.run(
                ["systemctl", "stop", "wpa_supplicant"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)
            
            # Unblock WiFi
            subprocess.run(
                ["rfkill", "unblock", "wifi"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Bring interface down and up
            subprocess.run(
                ["ip", "link", "set", "wlan0", "down"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1)
            subprocess.run(
                ["ip", "link", "set", "wlan0", "up"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(2)
            
            # Start wpa_supplicant with temp config
            result = subprocess.run(
                ["wpa_supplicant", "-B", "-i", "wlan0", "-c", temp_conf.name, "-f", "/tmp/wpa_test.log"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # Wait and check connection
            time.sleep(5)
            
            # Check if we got an IP
            result = subprocess.run(
                ["ip", "addr", "show", "wlan0"],
                capture_output=True,
                text=True,
                check=False
            )
            
            has_ip = "inet " in result.stdout and "127.0.0.1" not in result.stdout
            
            # Kill test wpa_supplicant
            subprocess.run(
                ["pkill", "-f", f"wpa_supplicant.*{temp_conf.name}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            # Clean up temp file
            try:
                os.unlink(temp_conf.name)
                os.unlink("/tmp/wpa_test.log")
            except:
                pass
            
            # Restart hostapd if it was running
            if hostapd_running:
                time.sleep(1)
                subprocess.run(
                    ["systemctl", "start", "hostapd"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            
            if has_ip:
                self.send_json_response(200, {
                    "success": True,
                    "message": "Successfully connected to WiFi network"
                })
            else:
                # Check log for errors
                error_msg = "Failed to connect"
                try:
                    if os.path.exists("/tmp/wpa_test.log"):
                        with open("/tmp/wpa_test.log", "r") as f:
                            log_content = f.read()
                            if "4-Way Handshake failed" in log_content:
                                error_msg = "Incorrect password"
                            elif "auth_failures" in log_content:
                                error_msg = "Authentication failed"
                except:
                    pass
                
                self.send_json_response(200, {
                    "success": False,
                    "error": error_msg
                })
        except subprocess.TimeoutExpired:
            self.send_json_response(500, {"error": "Connection test timeout"})
        except Exception as e:
            print(f"[config-server] Error testing WiFi: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def clear_wifi_credentials(self):
        """Clear WiFi credentials by removing network blocks from wpa_supplicant.conf."""
        try:
            # Call network-config.py to clear WiFi
            if not os.path.exists(NETWORK_CONFIG_SCRIPT):
                # Try local path
                local_script = os.path.join(os.path.dirname(__file__), "network-config.py")
                if os.path.exists(local_script):
                    script_path = local_script
                else:
                    self.send_json_response(500, {"error": "Network configuration script not found"})
                    return
            else:
                script_path = NETWORK_CONFIG_SCRIPT
            
            # Build command to clear WiFi
            cmd = ["python3", script_path, "--clear-wifi"]
            
            # Run configuration script
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                print(f"[config-server] WiFi credentials cleared successfully", flush=True)
                self.send_json_response(200, {
                    "message": "WiFi credentials cleared successfully. System will fall back to hotspot mode."
                })
            else:
                error_msg = result.stderr or result.stdout or "Unknown error"
                print(f"[config-server] Error clearing WiFi credentials: {error_msg}", flush=True)
                self.send_json_response(500, {"error": f"Failed to clear WiFi credentials: {error_msg}"})
        
        except subprocess.TimeoutExpired:
            print(f"[config-server] Timeout clearing WiFi credentials", flush=True)
            self.send_json_response(500, {"error": "Timeout while clearing WiFi credentials"})
        except Exception as e:
            print(f"[config-server] Error clearing WiFi credentials: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def handle_set_password(self):
        """Handle web interface password setting."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data.decode('utf-8'))
            
            password = data.get('password', '').strip()
            
            if not password:
                self.send_json_response(400, {"error": "Password is required"})
                return
            
            if len(password) < 4:
                self.send_json_response(400, {"error": "Password must be at least 4 characters"})
                return
            
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(WEB_PASSWORD_FILE), exist_ok=True)
            
            # Store password (in production, should be hashed, but for simplicity we'll store plaintext)
            with open(WEB_PASSWORD_FILE, 'w') as f:
                f.write(password)
            
            # Set restrictive permissions
            os.chmod(WEB_PASSWORD_FILE, 0o600)
            
            self.send_json_response(200, {
                "message": "Password set successfully. Please refresh the page and log in."
            })
        except Exception as e:
            print(f"[config-server] Error setting password: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def handle_reboot(self):
        """Handle system reboot."""
        try:
            # Use systemctl reboot (safer than direct reboot command)
            subprocess.Popen(
                ["systemctl", "reboot"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            self.send_json_response(200, {
                "message": "System reboot initiated"
            })
        except Exception as e:
            print(f"[config-server] Error rebooting: {e}", flush=True)
            self.send_json_response(500, {"error": str(e)})
    
    def send_json_response(self, status_code, data):
        """Send JSON response."""
        self.send_response(status_code)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
    
    def log_message(self, format, *args):
        """Override to use print instead of stderr."""
        print(f"[config-server] {format % args}", flush=True)


def main():
    """Start the configuration server."""
    try:
        with socketserver.TCPServer(("", CONFIG_SERVER_PORT), ConfigHandler) as httpd:
            print(f"[config-server] Configuration server started on port {CONFIG_SERVER_PORT}", flush=True)
            print(f"[config-server] Access at http://192.168.4.1:{CONFIG_SERVER_PORT}", flush=True)
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[config-server] Server stopped", flush=True)
    except Exception as e:
        print(f"[config-server] Server error: {e}", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

