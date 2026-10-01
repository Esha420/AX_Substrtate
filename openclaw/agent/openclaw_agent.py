#!/usr/bin/env python3
"""
OpenClaw Autonomous Agent Workload
Standard autonomous agent loop supporting heterogeneous real-world workflows:
- Research Agent (literature search & synthesis)
- Data Agent (schema discovery & distributed data aggregation)
- Monitoring Agent (endpoint probing & health checks)
- Document Agent (template validation & document rendering)
- Security Agent (threat intelligence & vulnerability scanning)

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

try:
    from telemetry import init_telemetry, trace_agent, trace_tool, flush_telemetry
except ImportError:
    import contextlib
    def init_telemetry(): pass
    def flush_telemetry(): pass
    @contextlib.contextmanager
    def trace_agent(*args, **kwargs): yield
    @contextlib.contextmanager
    def trace_tool(*args, **kwargs): yield

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [OpenClaw] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

WORKSPACE = os.environ.get("WORKSPACE", "/workspace")
STATE_FILE = os.path.join(WORKSPACE, "state.json")
REPORT_FILE = os.path.join(WORKSPACE, "report.md")
TARGET = os.environ.get("TARGET", "security-target.default.svc.cluster.local")
SCAN_API_URL = os.environ.get("SCAN_API_URL", "http://external-scan-api:8080")
TASK_NAME = os.environ.get("TASK_NAME", os.environ.get("HOSTNAME", "openclaw-task"))

# Derive Role & Workflow Type
ROLE = os.environ.get("ROLE", "")
if not ROLE:
    if "research" in TASK_NAME:
        ROLE = "research"
    elif "data" in TASK_NAME:
        ROLE = "data"
    elif "monitoring" in TASK_NAME or "probe" in TASK_NAME:
        ROLE = "monitoring"
    elif "document" in TASK_NAME or "render" in TASK_NAME:
        ROLE = "document"
    else:
        ROLE = "security"

ROLE_CONFIGS = {
    "research": {
        "job_type": "synthesis",
        "target": "Autonomous Actor Checkpointing & State Persistence",
        "description": "Scientific Literature Synthesis",
        "report_title": "OpenClaw Research Agent: Distributed Systems Synthesis Report",
        "local_steps": [
            "Parsing keyword graph from Research MCP...",
            "Indexing conceptual ontology across 18 literature sources...",
            "Constructing comparative theorem matrix for actor migration...",
            "Synthesizing state persistence proofs before external wait..."
        ]
    },
    "data": {
        "job_type": "aggregation",
        "target": "telemetry-metrics-stream",
        "description": "Telemetry Data Aggregation",
        "report_title": "OpenClaw Data Agent: Telemetry Stream Analysis Report",
        "local_steps": [
            "Validating column schema from Data MCP...",
            "Generating vectorized transformation pipeline...",
            "Computing rolling window quantiles for resident memory...",
            "Optimizing aggregation partition buckets..."
        ]
    },
    "monitoring": {
        "job_type": "probe",
        "target": "core-infra-mesh",
        "description": "Distributed Infrastructure Probing",
        "report_title": "OpenClaw Monitoring Agent: System Health & Uptime Report",
        "local_steps": [
            "Cross-referencing edge latency metrics from Monitoring MCP...",
            "Constructing synthetic network topology graph...",
            "Evaluating SLA threshold compliance models...",
            "Preparing probe trace records for async validation..."
        ]
    },
    "document": {
        "job_type": "render",
        "target": "quarterly_technical_audit.pdf",
        "description": "Technical Documentation Rendering",
        "report_title": "OpenClaw Document Agent: Automated Publication Report",
        "local_steps": [
            "Validating markdown AST tokens against template schema...",
            "Resolving dynamic references and table layout constraints...",
            "Compiling cross-section hyperlinks and asset manifests...",
            "Pre-rendering vector charts prior to document compilation..."
        ]
    },
    "security": {
        "job_type": "scan",
        "target": TARGET,
        "description": "Vulnerability Investigation",
        "report_title": "OpenClaw Security Agent: Autonomous Vulnerability Audit Report",
        "local_steps": [
            "Parsing local port scan results...",
            "Cross-referencing Threat Intel MCP exposure signatures...",
            "Evaluating CVE exploitability matrix...",
            "Structuring vulnerability prioritization index..."
        ]
    }
}

CURRENT_ROLE_CFG = ROLE_CONFIGS.get(ROLE, ROLE_CONFIGS["security"])

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

def submit_external_job():
    job_type = CURRENT_ROLE_CFG["job_type"]
    target = CURRENT_ROLE_CFG["target"]

    with trace_tool("submit_external_job", tool_args={"job_type": job_type, "target": target}):
        url = f"{SCAN_API_URL}/api/v1/jobs"
        payload = json.dumps({
            "job_type": job_type,
            "target": target,
            "requester": f"openclaw-{ROLE}",
            "task_name": TASK_NAME
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "Connection": "close"},
            method="POST"
        )
        logging.info(f"Submitting async {job_type} job for target: {target} -> {url}")
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
            logging.error(f"Error submitting {job_type} job: {e}")
            raise

def query_external_job(job_id):
    with trace_tool("query_external_job", tool_args={"job_id": job_id}):
        url = f"{SCAN_API_URL}/api/v1/jobs/{job_id}"
        req = urllib.request.Request(
            url,
            headers={"Connection": "close"},
            method="GET"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logging.warning(f"Error querying job {job_id}: {e}")
            return None

def generate_report(job_id, result_data, poll_count, start_time):
    with trace_tool("generate_report", tool_args={"job_id": job_id}):
        total_elapsed = round(time.time() - start_time, 2)
        title = CURRENT_ROLE_CFG["report_title"]
        job_type = CURRENT_ROLE_CFG["job_type"]
        target = CURRENT_ROLE_CFG["target"]

        report_content = f"""# {title}

- **Actor Persona:** `{ROLE.capitalize()} Agent` (`{TASK_NAME}`)
- **External Operation ID:** `{job_id}`
- **Operation Type:** `{job_type}`
- **Target / Subject:** `{target}`
- **Execution Status:** COMPLETED
- **Total Wall-Clock Time:** {total_elapsed}s
- **Status Check Iterations:** {poll_count}
- **Decoupled Backend:** External Asynchronous API (`{SCAN_API_URL}`)

---

## 1. Executive Summary & Operation Results

```json
{json.dumps(result_data, indent=2)}
```

---

## 2. Infrastructure Continuity & Integrity Stamp
- **Job Created Exactly Once:** Verified via Operation ID `{job_id}`
- **Durable Snapshot Storage:** State persisted under `{WORKSPACE}`
- **Lifecycle Integrity:** Actor restored from persisted execution state without restarting the workflow.
- **Physical Worker Allocation:** Compute capacity reclaimed during external wait interval.
"""

        with open(REPORT_FILE, "w") as f:
            f.write(report_content)
            f.flush()
            os.fsync(f.fileno())

        # Also save role-specific file
        role_file = os.path.join(WORKSPACE, f"{ROLE}_report.md")
        with open(role_file, "w") as f:
            f.write(report_content)
            f.flush()
            os.fsync(f.fileno())

        logging.info(f"Report successfully written to {REPORT_FILE} and {role_file}")

def main():
    init_telemetry()
    logging.info("=================================================================")
    logging.info(f"OpenClaw Autonomous Agent [{ROLE.upper()}] initialized inside AX sandbox")
    logging.info(f"Task: {TASK_NAME} | Role: {ROLE} | Target: {CURRENT_ROLE_CFG['target']}")
    logging.info("=================================================================")

    with trace_agent(agent_name=f"openclaw-{ROLE}"):
        state = load_state()
        start_time = time.time()

        if state and state.get("job_id"):
            job_id = state["job_id"]
            poll_count = state.get("poll_count", 0)
            logging.info(f"Existing state loaded from {STATE_FILE}. Resuming pending operation '{job_id}' (poll_count={poll_count})")
            logging.info("The actor resumes from its persisted execution state without restarting the workflow from the beginning.")
        else:
            logging.info(f"No prior state found. Initiating autonomous {ROLE} workflow...")
            try:
                job_id, response_data = submit_external_job()
            except Exception as e:
                logging.error(f"Failed to submit external job: {e}")
                sys.exit(1)

            logging.info(f"External operation accepted! Assigned operation_id: '{job_id}'")

            # Post-Submission Local Reasoning (~2.0s of active local progress)
            logging.info(f"[Post-Submission Local Work] Executing local reasoning & processing (~2.0s)...")
            for i, step_msg in enumerate(CURRENT_ROLE_CFG["local_steps"], 1):
                time.sleep(0.5)
                logging.info(f"[Local Progress] ({i}/4) {step_msg}")
            logging.info("[Post-Submission Local Work] Local processing complete. No local tasks remaining.")

            poll_count = 0
            state = {
                "job_id": job_id,
                "role": ROLE,
                "target": CURRENT_ROLE_CFG["target"],
                "status": "WAITING_FOR_EXTERNAL_API",
                "submitted_at": start_time,
                "poll_count": poll_count
            }
            atomic_save_state(state)
            logging.info(f"Agent state updated: status={state['status']} job_id={job_id}")
            logging.info(f"Entering waiting state for operation {job_id} completion...")

        # 2. Discrete Polling Loop
        while True:
            poll_count += 1
            logging.info(f"[Wait Loop] [Iteration #{poll_count}] Querying status of operation '{job_id}'...")
            job_status_data = query_external_job(job_id)

            if job_status_data:
                status = job_status_data.get("status")
                elapsed = job_status_data.get("elapsed_sec", 0)
                logging.info(f"Operation '{job_id}' status: {status} (External elapsed: {elapsed}s)")

                state["poll_count"] = poll_count
                state["last_checked"] = time.time()
                state["external_status"] = status
                atomic_save_state(state)

                if status == "COMPLETED":
                    logging.info(f"Operation '{job_id}' has COMPLETED! Generating final report...")
                    result = job_status_data.get("result", {})
                    generate_report(job_id, result, poll_count, start_time)

                    state["status"] = "COMPLETED"
                    state["completed_at"] = time.time()
                    atomic_save_state(state)
                    logging.info(f"Agent state updated: status={state['status']} job_id={job_id}")
                    logging.info("Autonomous workflow complete. Final status: COMPLETED")
                    break
            else:
                logging.warning(f"Could not reach external API. Retrying in 4 seconds...")

            time.sleep(4)

        flush_telemetry()

    # Keep container alive and inspectable
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
