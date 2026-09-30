#!/usr/bin/env python3
"""
StateProbe: Minimal In-RAM & Disk State Preservation Workload
Designed to independently prove Substrate stateful hibernation without
external application-level dependencies.
"""

import os
import sys
import time
import uuid
import socket
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
STATE_FILE = os.path.join(WORKSPACE, "state.txt")
HTTP_PORT = int(os.environ.get("PROBE_HTTP_PORT", "8080"))

# IN-RAM PROCESS STATE (Lives purely in heap/process memory)
SESSION_UUID = str(uuid.uuid4())[:8]
RAM_COUNTER = 0
PID = os.getpid()

def get_current_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def read_disk_counter():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                content = f.read().strip()
                if content.isdigit():
                    return int(content)
        except Exception:
            pass
    return 0

def write_disk_counter(count):
    os.makedirs(WORKSPACE, exist_ok=True)
    temp_file = STATE_FILE + ".tmp"
    with open(temp_file, "w") as f:
        f.write(str(count))
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, STATE_FILE)

class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global RAM_COUNTER
        data = {
            "session_uuid": SESSION_UUID,
            "pid": PID,
            "ram_counter": RAM_COUNTER,
            "disk_counter": read_disk_counter(),
            "worker_ip": get_current_ip(),
            "timestamp": time.time()
        }
        resp = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, format, *args):
        pass  # Suppress default HTTP logging to keep stdout clean

def start_http_server():
    server = HTTPServer(("0.0.0.0", HTTP_PORT), StatusHandler)
    server.serve_forever()

def main():
    global RAM_COUNTER
    print("=================================================================", flush=True)
    print(f"[StateProbe] Starting probe process PID={PID} | Session UUID={SESSION_UUID}", flush=True)
    print(f"[StateProbe] Workspace: {WORKSPACE} | HTTP status port: {HTTP_PORT}", flush=True)
    print("=================================================================", flush=True)

    # Start lightweight status server in background thread
    t = threading.Thread(target=start_http_server, daemon=True)
    t.start()

    while True:
        RAM_COUNTER += 1
        disk_counter = read_disk_counter() + 1
        write_disk_counter(disk_counter)
        current_ip = get_current_ip()

        print(
            f"[StateProbe] Session={SESSION_UUID} | PID={PID} | "
            f"Worker={current_ip} | RAM Count: {RAM_COUNTER} | Disk Count: {disk_counter}",
            flush=True
        )
        time.sleep(2)

if __name__ == "__main__":
    main()
