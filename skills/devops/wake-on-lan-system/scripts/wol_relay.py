#!/usr/bin/env python3
"""
WoL Relay Server — runs on Raspberry Pi (always-on).
Exposes HTTP endpoint that sends Wake-on-LAN magic packets.
Use with Tailscale for remote triggering.

Usage:
    WOL_TOKEN=your-secret python3 wol_relay.py
    curl -X POST -H "Authorization: Bearer your-secret" http://pi-ip:8080/wake
"""

import subprocess
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

DESKTOP_MAC = os.environ.get("WOL_MAC", "AA:BB:CC:DD:EE:FF")
DESKTOP_IP = os.environ.get("WOL_IP", "192.168.1.255")
API_TOKEN = os.environ.get("WOL_TOKEN", "")
PORT = int(os.environ.get("WOL_PORT", "8080"))

if not API_TOKEN:
    print("ERROR: Set WOL_TOKEN environment variable", file=sys.stderr)
    sys.exit(1)


class WoLHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]}", flush=True)

    def do_POST(self):
        if self.path == "/wake":
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {API_TOKEN}":
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"Unauthorized\n")
                return

            results = []
            
            # Method 1: Direct unicast (works if ARP cache exists)
            try:
                subprocess.run(
                    ["wakeonlan", DESKTOP_MAC],
                    timeout=5, capture_output=True
                )
                results.append("unicast: sent")
            except Exception as e:
                results.append(f"unicast: failed ({e})")

            # Method 2: Directed broadcast
            try:
                subprocess.run(
                    ["wakeonlan", "-i", DESKTOP_IP, DESKTOP_MAC],
                    timeout=5, capture_output=True
                )
                results.append("broadcast: sent")
            except Exception as e:
                results.append(f"broadcast: failed ({e})")

            response = "\n".join(results)
            print(f"  → {response}", flush=True)

            self.send_response(200)
            self.end_headers()
            self.wfile.write(f"OK\n{response}\n".encode())

        elif self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK\n")

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found\n")


if __name__ == "__main__":
    print(f"🔌 WoL Relay starting on port {PORT}")
    print(f"   MAC: {DESKTOP_MAC}   Broadcast: {DESKTOP_IP}")
    server = HTTPServer(("0.0.0.0", PORT), WoLHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()