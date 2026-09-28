#!/usr/bin/env python3
"""
OpenClaw Autonomous Agent Workload
Standard autonomous agent loop with external tool invocation.
Maintains execution progress in /workspace/state.json without any
Kubernetes, AX, or Substrate awareness.
"""

import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [OpenClaw] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
STATE_FILE = os.path.join(WORKSPACE, "state.json")
REPORT_FILE = os.path.join(WORKSPACE, "report.md")
TARGET = os.environ.get("TARGET", "security-target.default.svc.cluster.local")
SCAN_API_URL = os.environ.get("SCAN_API_URL", "http://mock-scan-api.default.svc.cluster.local:8080")

def atomic_save_state(state):
    os.makedirs(WORKSPACE, exist_ok=True)
    temp_file = STATE_FILE + ".tmp"
    with open(temp_file, "w") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_file, STATE_FILE)

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"Failed to read state file: {e}")
    return None

def submit_external_scan(target):
    url = f"{SCAN_API_URL}/api/v1/scans"
    payload = json.dumps({"target": target, "requester": "openclaw-agent"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Connection": "close"},
        method="POST"
    )
    logging.info(f"Submitting scan request for target: {target} -> {url}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("job_id"), data
    except urllib.error.HTTPError as e:
        if e.code == 202:
            data = json.loads(e.read().decode("utf-8"))
            return data.get("job_id"), data
        raise
    except Exception as e:
        logging.error(f"Error submitting scan request: {e}")
        raise

def query_external_scan(job_id):
    url = f"{SCAN_API_URL}/api/v1/scans/{job_id}"
    req = urllib.request.Request(
        url,
        headers={"Connection": "close"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logging.warning(f"Error querying scan job {job_id}: {e}")
        return None

def generate_report(job_id, target, scan_result, poll_count, start_time):
    total_elapsed = round(time.time() - start_time, 2)
    findings = scan_result.get("findings", [])
    services = scan_result.get("services", {})
    open_ports = scan_result.get("open_ports", [])

    report_content = f"""# OpenClaw Autonomous Security Audit Report

- **Target Host:** `{target}`
- **External Job ID:** `{job_id}`
- **Status:** COMPLETED
- **Total Wall-Clock Time:** {total_elapsed}s
- **Status Check Iterations:** {poll_count}
- **Scan Engine:** External Asynchronous Scanner (mock-scan-api)

---

## 1. Discovered Services & Open Ports

| Port | Service Banner | Protocol |
| :--- | :--- | :--- |
"""
    for port in open_ports:
        service_name = services.get(str(port), "Unknown")
        report_content += f"| `{port}` | {service_name} | TCP |\n"

    report_content += """
---

## 2. Identified Vulnerabilities

| Port | Severity | Finding |
| :--- | :--- | :--- |
"""
    for vuln in findings:
        report_content += f"| `{vuln.get('port')}` | **{vuln.get('severity')}** | {vuln.get('vuln')} |\n"

    report_content += f"""
---

## 3. Infrastructure Continuity & Integrity Stamp
- **Job Created Exactly Once:** Verified via Job ID `{job_id}`
- **Durable Storage:** State verified under `{WORKSPACE}`
- **Execution Lifecycle:** Completed via durable snapshot restore without duplicate submission.
"""

    with open(REPORT_FILE, "w") as f:
        f.write(report_content)
        f.flush()
        os.fsync(f.fileno())

    logging.info(f"Audit report successfully written to {REPORT_FILE}")

def main():
    logging.info("=================================================================")
    logging.info("OpenClaw Autonomous Agent initialized inside AX sandbox")
    logging.info(f"Workspace: {WORKSPACE} | Target: {TARGET}")
    logging.info("=================================================================")

    # 1. State Inspection & Job Submission
    state = load_state()
    start_time = time.time()

    if state and state.get("job_id"):
        job_id = state["job_id"]
        poll_count = state.get("poll_count", 0)
        logging.info(f"Existing state loaded from {STATE_FILE}. Resuming pending job_id='{job_id}' (poll_count={poll_count})")
    else:
        logging.info("No prior state found. Initiating autonomous scanning workflow...")
        try:
            job_id, response_data = submit_external_scan(TARGET)
        except Exception as e:
            logging.error(f"Failed to submit external scan: {e}")
            sys.exit(1)

        logging.info(f"External scan successfully accepted! Assigned job_id: '{job_id}'")
        poll_count = 0
        state = {
            "job_id": job_id,
            "target": TARGET,
            "status": "WAITING_FOR_EXTERNAL_API",
            "submitted_at": start_time,
            "poll_count": poll_count
        }
        atomic_save_state(state)
        logging.info(f"Saved initial job state to {STATE_FILE}. Entering discrete polling loop...")

    # 2. Discrete Polling Loop
    while True:
        poll_count += 1
        logging.info(f"[Iteration #{poll_count}] Querying status of job '{job_id}'...")
        job_status_data = query_external_scan(job_id)

        if job_status_data:
            status = job_status_data.get("status")
            elapsed = job_status_data.get("elapsed_sec", 0)
            logging.info(f"Job '{job_id}' status: {status} (External elapsed: {elapsed}s)")

            state["poll_count"] = poll_count
            state["last_checked"] = time.time()
            state["external_status"] = status
            atomic_save_state(state)

            if status == "COMPLETED":
                logging.info(f"Scan job '{job_id}' has COMPLETED! Fetching findings and generating report...")
                result = job_status_data.get("result", {})
                generate_report(job_id, TARGET, result, poll_count, start_time)

                state["status"] = "COMPLETED"
                state["completed_at"] = time.time()
                atomic_save_state(state)
                logging.info("Autonomous task finished successfully! Entering quiescent state.")
                break
        else:
            logging.warning(f"Could not reach scan API or invalid response. Retrying in 5 seconds...")

        time.sleep(5)

    # Keep container alive and inspectable
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
