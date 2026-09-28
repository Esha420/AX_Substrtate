#!/usr/bin/env python3
"""
Mock External Async Scan API Service
Simulates a long-running, asynchronous security scanning API.
Returns 202 Accepted + Job ID on submission, executes a 60-second background scan,
and dispatches an HTTP webhook callback upon job completion to trigger automated AX resume.
"""

import json
import logging
import os
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MockScanAPI] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

PORT = int(os.environ.get("PORT", "8080"))
JOB_DELAY_SECONDS = int(os.environ.get("JOB_DELAY_SECONDS", "60"))
DEFAULT_CALLBACK_URL = os.environ.get("CALLBACK_URL", "http://callback-receiver.ax-system.svc.cluster.local:8080/webhook")

# In-memory store for jobs and audit logs
jobs_lock = threading.Lock()
jobs = {}
audit_log = []

def run_job_background(job_id, target, delay, callback_url, requester):
    logging.info(f"Background worker started for job {job_id} on target {target}. Duration: {delay}s")
    time.sleep(delay)
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["status"] = "COMPLETED"
            jobs[job_id]["completed_at"] = time.time()
            jobs[job_id]["result"] = {
                "target": target,
                "scan_type": "deep_vulnerability_scan",
                "open_ports": [22, 80, 8080, 3306],
                "services": {
                    "22": "OpenSSH 8.9p1 Ubuntu",
                    "80": "nginx 1.24.0",
                    "8080": "Apache httpd 2.4.52",
                    "3306": "MySQL 5.7.44"
                },
                "findings": [
                    {"port": 22, "severity": "HIGH", "vuln": "CVE-2023-38408 (PKCS#11 provider RCE in OpenSSH)"},
                    {"port": 8080, "severity": "MEDIUM", "vuln": "Apache Server Tokens Exposed"},
                    {"port": 3306, "severity": "LOW", "vuln": "MySQL Unencrypted Client Protocol Allowed"}
                ],
                "summary": "External scan finished with 3 vulnerabilities discovered."
            }
            logging.info(f"Background worker completed for job {job_id}!")

    # Dispatch external webhook callback
    if callback_url:
        logging.info(f"Dispatching completion webhook for job {job_id} to {callback_url}...")
        task_name = "openclaw-task"
        atespace = "default"
        webhook_payload = json.dumps({
            "event": "JOB_COMPLETED",
            "job_id": job_id,
            "target": target,
            "requester": requester,
            "task_name": task_name,
            "atespace": atespace,
            "status": "COMPLETED",
            "timestamp": time.time()
        }).encode("utf-8")

        try:
            req = urllib.request.Request(
                callback_url,
                data=webhook_payload,
                headers={"Content-Type": "application/json", "Connection": "close"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = resp.read().decode("utf-8")
                logging.info(f"Webhook response ({resp.status}): {resp_body}")
                with jobs_lock:
                    audit_log.append({
                        "event": "WEBHOOK_DISPATCHED",
                        "job_id": job_id,
                        "callback_url": callback_url,
                        "status_code": resp.status,
                        "response": resp_body,
                        "timestamp": time.time()
                    })
        except Exception as e:
            logging.error(f"Failed to dispatch webhook for job {job_id} to {callback_url}: {e}")
            with jobs_lock:
                audit_log.append({
                    "event": "WEBHOOK_FAILED",
                    "job_id": job_id,
                    "callback_url": callback_url,
                    "error": str(e),
                    "timestamp": time.time()
                })

class MockScanHandler(BaseHTTPRequestHandler):
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
            self._send_json(200, {"status": "HEALTHY", "service": "mock-scan-api"})
            return

        if path == "/api/v1/jobs" or path == "/api/v1/audit":
            with jobs_lock:
                self._send_json(200, {
                    "total_jobs": len(jobs),
                    "jobs": jobs,
                    "audit_trail": audit_log
                })
            return

        if path.startswith("/api/v1/scans/"):
            job_id = path.split("/")[-1]
            with jobs_lock:
                if job_id not in jobs:
                    self._send_json(404, {"error": "Job not found", "job_id": job_id})
                    return
                job = jobs[job_id]
                elapsed = time.time() - job["created_at"]
                self._send_json(200, {
                    "job_id": job_id,
                    "status": job["status"],
                    "elapsed_sec": round(elapsed, 1),
                    "target": job["target"],
                    "result": job.get("result")
                })
            return

        self._send_json(404, {"error": "Not found", "path": self.path})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/v1/scans":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                payload = {}

            target = payload.get("target", "security-target.default.svc.cluster.local")
            requester = payload.get("requester", "openclaw-agent")
            callback_url = payload.get("callback_url", DEFAULT_CALLBACK_URL)

            with jobs_lock:
                job_index = len(jobs) + 1
                job_id = f"scan-{job_index:03d}"
                created_at = time.time()
                job_entry = {
                    "job_id": job_id,
                    "target": target,
                    "requester": requester,
                    "callback_url": callback_url,
                    "status": "PENDING",
                    "created_at": created_at,
                    "estimated_duration_sec": JOB_DELAY_SECONDS,
                    "result": None
                }
                jobs[job_id] = job_entry
                audit_log.append({
                    "event": "JOB_SUBMITTED",
                    "job_id": job_id,
                    "target": target,
                    "requester": requester,
                    "callback_url": callback_url,
                    "timestamp": created_at
                })

            logging.info(f"Received scan request for '{target}'. Assigned job_id: {job_id}. Returning 202 Accepted.")
            
            # Start asynchronous background execution
            worker_thread = threading.Thread(
                target=run_job_background,
                args=(job_id, target, JOB_DELAY_SECONDS, callback_url, requester),
                daemon=True
            )
            worker_thread.start()

            # Return 202 Accepted
            self._send_json(202, {
                "job_id": job_id,
                "status": "PENDING",
                "message": f"Scan job accepted. Processing will take ~{JOB_DELAY_SECONDS} seconds.",
                "estimated_duration_sec": JOB_DELAY_SECONDS,
                "poll_endpoint": f"/api/v1/scans/{job_id}",
                "callback_url": callback_url
            })
            return

        self._send_json(404, {"error": "Endpoint not found"})

def main():
    server = HTTPServer(("0.0.0.0", PORT), MockScanHandler)
    logging.info(f"Mock Scan API listening on port {PORT} (Job delay: {JOB_DELAY_SECONDS}s, Callback: {DEFAULT_CALLBACK_URL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down Mock Scan API...")
        server.server_close()

if __name__ == "__main__":
    main()
