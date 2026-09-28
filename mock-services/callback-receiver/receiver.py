#!/usr/bin/env python3
"""
Callback Receiver Microservice for AX Task Resumption
Listens for external API completion webhooks and automatically triggers
AX task resumption via the AX CLI / gRPC control plane.
"""

import json
import logging
import os
import subprocess
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [CallbackReceiver] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

PORT = int(os.environ.get("PORT", "8080"))
AX_SERVER_URL = os.environ.get("AX_SERVER_URL", "http://ax-server.ax-system.svc.cluster.local:8080")
AX_CLI_BIN = os.environ.get("AX_CLI_BIN", "/usr/local/bin/ax")

callback_history = []

def trigger_ax_resume(task_name, atespace="default"):
    cmd = [
        AX_CLI_BIN,
        "resume",
        "task",
        task_name,
        "-a",
        atespace,
        "--server",
        AX_SERVER_URL
    ]
    logging.info(f"Invoking AX Resume command: {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15
        )
        return proc.returncode, proc.stdout, proc.stderr
    except Exception as e:
        return -1, "", str(e)

class CallbackHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code, data):
        response_bytes = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "" or path == "/healthz":
            self._send_json(200, {
                "status": "HEALTHY",
                "service": "callback-receiver",
                "ax_server": AX_SERVER_URL
            })
            return

        if path == "/api/v1/callbacks" or path == "/api/v1/history":
            self._send_json(200, {
                "total_events": len(callback_history),
                "history": callback_history
            })
            return

        self._send_json(404, {"error": "Not found", "path": self.path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/webhook" or path == "/callback":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except Exception as e:
                self._send_json(400, {"error": f"Invalid JSON payload: {e}"})
                return

            event_type = payload.get("event", "UNKNOWN")
            job_id = payload.get("job_id", "unknown")
            task_name = payload.get("task_name", "openclaw-task")
            atespace = payload.get("atespace", "default")
            received_at = time.time()

            logging.info(f"Received webhook callback: event='{event_type}', job_id='{job_id}', task='{task_name}', atespace='{atespace}'")

            # Execute AX Resume
            ret_code, stdout, stderr = trigger_ax_resume(task_name, atespace)

            history_entry = {
                "received_at": received_at,
                "event": event_type,
                "job_id": job_id,
                "task_name": task_name,
                "atespace": atespace,
                "exit_code": ret_code,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
                "success": (ret_code == 0)
            }
            callback_history.append(history_entry)

            if ret_code == 0:
                logging.info(f"Successfully resumed AX task '{task_name}'! (Output: {stdout.strip()})")
                self._send_json(200, {
                    "status": "RESUME_SUCCESS",
                    "task": task_name,
                    "atespace": atespace,
                    "job_id": job_id,
                    "ax_output": stdout.strip()
                })
            else:
                logging.error(f"Failed to resume AX task '{task_name}' (exit code {ret_code}): {stderr.strip()}")
                self._send_json(500, {
                    "status": "RESUME_FAILED",
                    "task": task_name,
                    "atespace": atespace,
                    "job_id": job_id,
                    "error": stderr.strip()
                })
            return

        self._send_json(404, {"error": "Endpoint not found"})

def main():
    server = HTTPServer(("0.0.0.0", PORT), CallbackHandler)
    logging.info(f"AX Callback Receiver listening on port {PORT} (AX Server: {AX_SERVER_URL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down Callback Receiver...")
        server.server_close()

if __name__ == "__main__":
    main()
