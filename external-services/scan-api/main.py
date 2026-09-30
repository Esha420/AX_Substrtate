#!/usr/bin/env python3
"""
External Async Security Scan API Service
Runs outside Kubernetes on the kind bridge network.
Provides 202 Accepted + Job ID async contract, background scan execution,
and completion webhook dispatched into atenet-router on NodePort 30080.
Completely decoupled from AX and Substrate.
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
    format="%(asctime)s [%(levelname)s] [ExternalScanAPI] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

PORT = int(os.environ.get("PORT", "8080"))
JOB_DELAY_SECONDS = int(os.environ.get("JOB_DELAY_SECONDS", "45"))
DEFAULT_CALLBACK_URL = os.environ.get("CALLBACK_URL", "http://kind-control-plane:30080/webhook")

jobs_lock = threading.Lock()
jobs = {}
audit_log = []

def run_job_background(job_id, target, delay, callback_url, requester, task_name, atespace):
    logging.info(f"Background worker started for job '{job_id}' (target='{target}', duration={delay}s, task='{task_name}')")
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
            logging.info(f"Background worker completed for job '{job_id}'!")

    # Dispatch external completion webhook into atenet-router to trigger actor wake
    if callback_url:
        logging.info(f"Dispatching completion webhook for job '{job_id}' to {callback_url} (target actor: {atespace}/{task_name})...")
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

        headers = {
            "Content-Type": "application/json",
            "Connection": "close",
            "ate-target-actor": f"{atespace}/{task_name}"
        }

        try:
            req = urllib.request.Request(
                callback_url,
                data=webhook_payload,
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = resp.read().decode("utf-8")
                logging.info(f"Webhook delivered ({resp.status}): {resp_body}")
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
            logging.error(f"Failed to dispatch webhook for job '{job_id}' to {callback_url}: {e}")
            with jobs_lock:
                audit_log.append({
                    "event": "WEBHOOK_FAILED",
                    "job_id": job_id,
                    "callback_url": callback_url,
                    "error": str(e),
                    "timestamp": time.time()
                })

class ScanHandler(BaseHTTPRequestHandler):
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

        if path in ["", "/healthz"]:
            self._send_json(200, {"status": "HEALTHY", "service": "external-scan-api", "port": PORT})
            return

        if path in ["/api/v1/jobs", "/api/v1/audit"]:
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
            task_name = payload.get("task_name", os.environ.get("DEFAULT_TARGET_TASK", "openclaw-task"))
            atespace = payload.get("atespace", "default")
            callback_url = payload.get("callback_url", DEFAULT_CALLBACK_URL)

            with jobs_lock:
                job_index = len(jobs) + 1
                job_id = f"ext-scan-{job_index:03d}"
                created_at = time.time()
                job_entry = {
                    "job_id": job_id,
                    "target": target,
                    "requester": requester,
                    "task_name": task_name,
                    "atespace": atespace,
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
                    "task_name": task_name,
                    "atespace": atespace,
                    "callback_url": callback_url,
                    "timestamp": created_at
                })

            logging.info(f"Accepted scan request for '{target}'. Assigned job_id: {job_id} (Duration: {JOB_DELAY_SECONDS}s).")

            worker_thread = threading.Thread(
                target=run_job_background,
                args=(job_id, target, JOB_DELAY_SECONDS, callback_url, requester, task_name, atespace),
                daemon=True
            )
            worker_thread.start()

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
    server = HTTPServer(("0.0.0.0", PORT), ScanHandler)
    logging.info(f"External Scan API listening on port {PORT} (Duration: {JOB_DELAY_SECONDS}s, Callback: {DEFAULT_CALLBACK_URL})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down External Scan API...")
        server.server_close()

if __name__ == "__main__":
    main()
