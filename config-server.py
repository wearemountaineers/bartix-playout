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
import subprocess
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


class ConfigHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler for configuration server."""
    
    def do_GET(self):
        """Handle GET requests."""
        parsed_path = urllib.parse.urlparse(self.path)
        
        if parsed_path.path == "/":
            self.serve_config_form()
        elif parsed_path.path == "/status":
            self.serve_status()
        else:
            self.send_error(404, "Not Found")
    
    def do_POST(self):
        """Handle POST requests."""
        parsed_path = urllib.parse.urlparse(self.path)
        
        if parsed_path.path == "/configure":
            self.handle_configure()
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
            
            status = {
                "has_ip": has_ip,
                "has_internet": has_internet,
                "hotspot_running": hotspot_running,
                "network_type": "unknown"  # Could be enhanced to detect actual type
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

