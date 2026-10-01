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
JOB_DELAY_SECONDS = int(os.environ.get("JOB_DELAY_SECONDS", "25"))
DEFAULT_CALLBACK_URL = os.environ.get("CALLBACK_URL", "http://kind-control-plane:30088/webhook")
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://kind-control-plane:30088")

jobs_lock = threading.Lock()
jobs = {}
audit_log = []

def run_job_background(job_id, job_type, target, delay, callback_url, requester, task_name, atespace):
    logging.info(f"Background worker started for job '{job_id}' (type='{job_type}', target='{target}', duration={delay}s, task='{task_name}')")
    time.sleep(delay)

    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]["status"] = "COMPLETED"
            jobs[job_id]["completed_at"] = time.time()

            if job_type in ("synthesis", "research"):
                result_payload = {
                    "topic": target or "Autonomous Actor Frameworks",
                    "job_type": "literature_synthesis",
                    "papers_analyzed": 18,
                    "synthesis_summary": "Comprehensive analysis of actor checkpointing and memory reclamation in distributed agent systems.",
                    "key_insights": [
                        "State persistence across actor migrations via zero-copy snapshots achieves sub-second restoration.",
                        "Decoupling ingress routing from external job IDs eliminates internal actor identity leakage.",
                        "Dynamic worker reclamation yields up to 70% compute savings during asynchronous wait intervals."
                    ],
                    "status": "SYNTHESIS_COMPLETE"
                }
            elif job_type in ("aggregation", "data"):
                result_payload = {
                    "dataset": target or "telemetry-stream",
                    "job_type": "data_aggregation",
                    "records_aggregated": 1420500,
                    "aggregations": {
                        "mean_cpu_pct": 24.8,
                        "p95_cpu_pct": 68.2,
                        "avg_resident_memory_mb": 142.6,
                        "error_count": 0
                    },
                    "status": "AGGREGATION_COMPLETE"
                }
            elif job_type in ("probe", "monitoring"):
                result_payload = {
                    "target_group": target or "infra-endpoints",
                    "job_type": "health_probe",
                    "probed_endpoints": ["edge-01", "edge-02", "api-gateway", "storage-s3"],
                    "availability": "100%",
                    "avg_latency_ms": 3.8,
                    "sla_violation": False,
                    "status": "SYSTEMS_OPERATIONAL"
                }
            elif job_type in ("render", "document"):
                result_payload = {
                    "document_name": target or "technical_audit_v2.pdf",
                    "job_type": "document_rendering",
                    "pages_generated": 14,
                    "format": "PDF/A-1b",
                    "embedded_assets": ["topology_diagram.png", "metrics_table.csv"],
                    "file_size_kb": 1840,
                    "status": "RENDER_SUCCESS"
                }
            else: # scan / security
                result_payload = {
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

            jobs[job_id]["result"] = result_payload
            logging.info(f"Background worker completed for job '{job_id}' (type={job_type})!")

    # Dispatch external completion webhook into Operation Correlation Bridge
    if callback_url:
        logging.info(f"Dispatching completion webhook for operation '{job_id}' to {callback_url} (Decoupled: no actor identity)...")
        webhook_payload = json.dumps({
            "event": "JOB_COMPLETED",
            "operation_id": job_id,
            "status": "COMPLETED",
            "timestamp": time.time()
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Connection": "close"
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

        if path in ["", "/", "/healthz"]:
            self._send_json(200, {"status": "HEALTHY", "service": "external-async-jobs-api", "port": PORT})
            return

        if path in ["/api/v1/jobs", "/api/v1/audit"]:
            with jobs_lock:
                self._send_json(200, {
                    "total_jobs": len(jobs),
                    "jobs": jobs,
                    "audit_trail": audit_log
                })
            return

        if path.startswith("/api/v1/scans/") or path.startswith("/api/v1/jobs/"):
            job_id = path.split("/")[-1]
            with jobs_lock:
                if job_id not in jobs:
                    self._send_json(404, {"error": "Job not found", "job_id": job_id})
                    return
                job = jobs[job_id]
                elapsed = time.time() - job["created_at"]
                self._send_json(200, {
                    "job_id": job_id,
                    "job_type": job.get("job_type", "scan"),
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

        if path in ("/api/v1/scans", "/api/v1/jobs"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else b"{}"
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except Exception:
                payload = {}

            task_name = payload.get("task_name", os.environ.get("DEFAULT_TARGET_TASK", "openclaw-task"))
            requester = payload.get("requester", task_name)
            atespace = payload.get("atespace", "default")
            callback_url = payload.get("callback_url", DEFAULT_CALLBACK_URL)

            # Determine job type from payload or task name
            job_type = payload.get("job_type")
            if not job_type:
                if "research" in task_name:
                    job_type = "synthesis"
                elif "data" in task_name:
                    job_type = "aggregation"
                elif "monitoring" in task_name or "probe" in task_name:
                    job_type = "probe"
                elif "document" in task_name or "render" in task_name:
                    job_type = "render"
                else:
                    job_type = "scan"

            target = payload.get("target")
            if not target:
                if job_type == "synthesis":
                    target = "Distributed Actor Frameworks & Multiplexing"
                elif job_type == "aggregation":
                    target = "financial_telemetry_stream"
                elif job_type == "probe":
                    target = "core-infra-mesh"
                elif job_type == "render":
                    target = "technical_audit_v2.pdf"
                else:
                    target = "security-target.default.svc.cluster.local"

            # Determine duration
            default_durations = {
                "synthesis": 18,
                "aggregation": 20,
                "probe": 22,
                "render": 24,
                "scan": 26
            }
            delay_sec = int(payload.get("duration_sec", default_durations.get(job_type, JOB_DELAY_SECONDS)))

            with jobs_lock:
                job_index = len(jobs) + 1
                prefix_map = {
                    "synthesis": "res",
                    "aggregation": "dat",
                    "probe": "mon",
                    "render": "doc",
                    "scan": "sec"
                }
                job_pfx = prefix_map.get(job_type, "job")
                job_id = f"ext-{job_pfx}-{job_index:03d}"
                created_at = time.time()
                job_entry = {
                    "job_id": job_id,
                    "job_type": job_type,
                    "target": target,
                    "requester": requester,
                    "task_name": task_name,
                    "atespace": atespace,
                    "callback_url": callback_url,
                    "status": "PENDING",
                    "created_at": created_at,
                    "estimated_duration_sec": delay_sec,
                    "result": None
                }
                jobs[job_id] = job_entry
                audit_log.append({
                    "event": "JOB_SUBMITTED",
                    "job_id": job_id,
                    "job_type": job_type,
                    "target": target,
                    "requester": requester,
                    "task_name": task_name,
                    "atespace": atespace,
                    "callback_url": callback_url,
                    "timestamp": created_at
                })

            logging.info(f"Accepted {job_type} job for '{target}' from '{task_name}'. Assigned job_id: {job_id} (Duration: {delay_sec}s).")

            # Register operation with Correlation Bridge
            try:
                reg_payload = json.dumps({
                    "operation_id": job_id,
                    "actor": f"{atespace}/{task_name}",
                    "operation_type": job_type
                }).encode("utf-8")
                reg_req = urllib.request.Request(
                    f"{BRIDGE_URL}/api/v1/operations",
                    data=reg_payload,
                    headers={"Content-Type": "application/json", "Connection": "close"},
                    method="POST"
                )
                with urllib.request.urlopen(reg_req, timeout=3) as reg_resp:
                    logging.info(f"Registered operation '{job_id}' with Correlation Bridge (Status: {reg_resp.status})")
            except Exception as e:
                logging.warning(f"Could not register operation with bridge: {e}")

            worker_thread = threading.Thread(
                target=run_job_background,
                args=(job_id, job_type, target, delay_sec, callback_url, requester, task_name, atespace),
                daemon=True
            )
            worker_thread.start()

            self._send_json(202, {
                "job_id": job_id,
                "job_type": job_type,
                "status": "PENDING",
                "message": f"{job_type} job accepted. Processing will take ~{delay_sec} seconds.",
                "estimated_duration_sec": delay_sec,
                "poll_endpoint": f"/api/v1/jobs/{job_id}",
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
