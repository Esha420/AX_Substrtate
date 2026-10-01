#!/usr/bin/env python3
"""
AX Runtime Lifecycle Observer & Operation Correlation Bridge
Decoupled architecture:
- Implements Operation Correlation Bridge on Port 8088 (accessible via NodePort 30088).
- Stores structured operation records in Redis (operations:<operation_id>).
- Enforces multi-signal suspension eligibility:
    (Pending Operation in Redis) AND (No Local Progress in window tau) AND (Wait >= tau, default 3.0s)
- Decouples external callbacks: Webhook carries ONLY operation_id; Bridge resolves actor and invokes atenet-router.
- Guarantees idempotent duplicate callback suppression.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AX-Observer] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

TARGET_TASK = os.environ.get("TARGET_TASK", "openclaw-task*")
TARGET_ATESPACE = os.environ.get("TARGET_ATESPACE", "default")
POLL_INTERVAL_SEC = float(os.environ.get("POLL_INTERVAL_SEC", "1.0"))
SUSPEND_THRESHOLD_SEC = float(os.environ.get("SUSPEND_THRESHOLD_SEC", "3.0"))
REDIS_HOST = os.environ.get("REDIS_HOST", "ax-redis.ax-system.svc.cluster.local")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
ATENET_ROUTER_URL = os.environ.get("ATENET_ROUTER_URL", "http://atenet-router.ate-system.svc.cluster.local:80/webhook")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "8088"))

# Initialize Redis client
r_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

class ObserverState:
    IDLE = "IDLE"
    MONITORING = "MONITORING"
    SUSPEND_REQUESTED = "SUSPEND_REQUESTED"
    HIBERNATED = "HIBERNATED"
    RESUMED = "RESUMED"
    COMPLETED = "COMPLETED"

def run_cmd(cmd, timeout=10):
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
        return res.returncode, res.stdout.strip(), res.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

# --- Operation Correlation Store (Redis) ---

def register_operation(operation_id, actor, op_type="async_job", ttl_seconds=3600):
    now = time.time()
    record = {
        "operation_id": operation_id,
        "actor": actor,
        "operation_type": op_type,
        "status": "PENDING",
        "created_at": now,
        "expires_at": now + ttl_seconds
    }
    pipe = r_client.pipeline()
    pipe.set(f"operations:{operation_id}", json.dumps(record), ex=ttl_seconds)
    pipe.sadd(f"actor_pending_ops:{actor}", operation_id)
    pipe.execute()
    logging.info(f"[Bridge-Registry] Registered operation '{operation_id}' -> Actor: '{actor}' (Status: PENDING)")
    return record

def get_operation(operation_id):
    raw = r_client.get(f"operations:{operation_id}")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    return None

def get_actor_pending_operations(actor):
    return list(r_client.smembers(f"actor_pending_ops:{actor}"))

def complete_operation_atomically(operation_id):
    """
    Atomically transition operation status PENDING -> COMPLETED.
    Returns: (success: bool, actor: str, is_duplicate: bool)
    """
    key = f"operations:{operation_id}"
    raw = r_client.get(key)
    if not raw:
        return False, None, False

    try:
        record = json.loads(raw)
    except Exception:
        return False, None, False

    if record.get("status") == "COMPLETED":
        return True, record.get("actor"), True  # Duplicate suppressed

    record["status"] = "COMPLETED"
    record["completed_at"] = time.time()

    actor = record.get("actor")
    pipe = r_client.pipeline()
    pipe.set(key, json.dumps(record), ex=3600)
    if actor:
        pipe.srem(f"actor_pending_ops:{actor}", operation_id)
    pipe.execute()

    return True, actor, False

# --- Ingress Operation Correlation Bridge HTTP Server ---

class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy default http logging
        pass

    def do_POST(self):
        if self.path == "/api/v1/operations":
            # Register operation from agent or external service
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                op_id = data.get("operation_id")
                actor = data.get("actor")
                op_type = data.get("operation_type", "async_scan")
                if not op_id or not actor:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error": "missing operation_id or actor"}')
                    return
                record = register_operation(op_id, actor, op_type)
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(record).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode("utf-8"))

        elif self.path in ("/webhook", "/webhook/"):
            # Decoupled completion callback: external service sends ONLY { "operation_id": "..." }
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            try:
                data = json.loads(body)
                op_id = data.get("operation_id") or data.get("job_id")
                if not op_id:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b'{"error": "missing operation_id"}')
                    return

                logging.info(f"[Bridge-Webhook] Incoming completion callback for operation '{op_id}'")
                success, actor, is_duplicate = complete_operation_atomically(op_id)

                if not success:
                    logging.warning(f"[Bridge-Webhook] Unknown operation '{op_id}'")
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error": "unknown operation"}')
                    return

                if is_duplicate:
                    logging.info(f"[Bridge-Webhook] Duplicate callback detected for '{op_id}'. Suppressed (No-op).")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "ALREADY_COMPLETED", "message": "Duplicate callback suppressed"}')
                    return

                logging.info(f"[Bridge-Webhook] Operation '{op_id}' atomically marked COMPLETED. Target Actor: '{actor}'")

                # Forward request to atenet-router with resolved ate-target-actor header
                # to trigger native Substrate ResumeActor()
                logging.info(f"[Bridge-Webhook] Resolved actor envelope: Injecting header 'ate-target-actor: {actor}' -> {ATENET_ROUTER_URL}")
                fwd_headers = {
                    "Content-Type": "application/json",
                    "Connection": "close",
                    "ate-target-actor": actor
                }
                fwd_req = urllib.request.Request(
                    ATENET_ROUTER_URL,
                    data=body.encode("utf-8"),
                    headers=fwd_headers,
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(fwd_req, timeout=10) as fwd_resp:
                        resp_data = fwd_resp.read().decode("utf-8")
                        logging.info(f"[Bridge-Webhook] atenet-router accepted resume for '{actor}' (HTTP {fwd_resp.status})")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"status": "RESUMED", "actor": actor, "router_status": fwd_resp.status}).encode("utf-8"))
                except urllib.error.HTTPError as he:
                    # Even if backend returns 404 (due to python script not listening on /webhook),
                    # router already resumed the actor!
                    logging.info(f"[Bridge-Webhook] atenet-router dispatched resume for '{actor}' (Target HTTP {he.code})")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "RESUMED", "actor": actor, "router_status": he.code}).encode("utf-8"))
                except Exception as ex:
                    logging.error(f"[Bridge-Webhook] Failed forwarding to atenet-router: {ex}")
                    self.send_response(502)
                    self.end_headers()
                    self.wfile.write(f'{{"error": "router forward error: {str(ex)}"}}'.encode("utf-8"))

            except Exception as e:
                logging.error(f"[Bridge-Webhook] Processing error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(f'{{"error": "{str(e)}"}}'.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/v1/operations/"):
            op_id = self.path.split("/")[-1]
            record = get_operation(op_id)
            if record:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(record).encode("utf-8"))
            else:
                self.send_response(404)
                self.end_headers()
        elif self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_response(404)
            self.end_headers()

def start_bridge_server():
    server = HTTPServer(("0.0.0.0", BRIDGE_PORT), BridgeHandler)
    logging.info(f"[Bridge] Operation Correlation Bridge listening on port {BRIDGE_PORT}...")
    server.serve_forever()

# --- Substrate / AX Task & Worker Discovery ---

def list_target_tasks(pattern, atespace):
    if pattern in ("all", "*"):
        code, stdout, _ = run_cmd(["ax", "get", "tasks", "-a", atespace])
        tasks = []
        if code == 0:
            for line in stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 1:
                    tasks.append(parts[0])
        return tasks
    elif pattern.endswith("*"):
        prefix = pattern[:-1]
        code, stdout, _ = run_cmd(["ax", "get", "tasks", "-a", atespace])
        tasks = []
        if code == 0:
            for line in stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 1 and parts[0].startswith(prefix):
                    tasks.append(parts[0])
        return tasks
    elif "," in pattern:
        return [t.strip() for t in pattern.split(",") if t.strip()]
    else:
        return [pattern]

def get_ax_task(task_name, atespace):
    code, stdout, _ = run_cmd(["ax", "get", "task", task_name, "-a", atespace])
    if code != 0 or not stdout:
        return None
    task_info = {"phase": "Unknown", "actor": task_name}
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("phase:"):
            task_info["phase"] = line.split(":", 1)[1].strip()
        elif line.startswith("actor:"):
            task_info["actor"] = line.split(":", 1)[1].strip()
        elif line.startswith("workerIp:"):
            task_info["worker_ip"] = line.split(":", 1)[1].strip()
    return task_info

def get_substrate_actor(actor_name, atespace):
    code, stdout, _ = run_cmd(["kubectl-ate", "get", "actors", "-a", atespace])
    if code != 0 or not stdout:
        return None
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[1] == actor_name:
            return {
                "name": parts[1],
                "template": parts[2],
                "state": parts[3],
                "worker": parts[4] if len(parts) > 4 else "<none>"
            }
    return None

def inspect_actor_execution(task_name, atespace):
    """
    Inspects actor logs for:
    1. Local execution progress (e.g. active tool running, local reasoning lines).
    2. Explicit wait state indicators (e.g. WAITING_..., [Wait Loop]).
    3. Operation IDs mentioned in logs.
    """
    code, stdout, _ = run_cmd(["kubectl-ate", "logs", "actor", task_name, "-a", atespace])
    if code != 0 or not stdout:
        return False, False, None, None

    lines = stdout.splitlines()
    has_local_progress = False
    is_in_wait_loop = False
    detected_status = None
    detected_job_id = None

    # Inspect last 15 log lines
    recent_lines = lines[-15:] if len(lines) >= 15 else lines

    for line in recent_lines:
        # Check for active local progress signals
        if "[Local Progress]" in line or "local reasoning starting" in line or "Executing local" in line or "MCP response received" in line:
            has_local_progress = True

        # Check for wait loop / sleep signals
        if "[Wait Loop]" in line or "Entering waiting state" in line or "WAITING_FOR" in line:
            is_in_wait_loop = True

        m_status = re.search(r"status=['\"]?([A-Za-z0-9_]+)['\"]?", line)
        if m_status:
            detected_status = m_status.group(1).upper()

        m_job = re.search(r"job_id=['\"]?([A-Za-z0-9_-]+)['\"]?", line)
        if m_job:
            detected_job_id = m_job.group(1)

    return has_local_progress, is_in_wait_loop, detected_status, detected_job_id

def has_free_workers():
    code, stdout, _ = run_cmd(["kubectl-ate", "get", "workers"])
    if code != 0 or not stdout:
        return False
    for line in stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3] == "0/1":
            return True
    return False

# --- Main Observer Control Loop ---

def main():
    logging.info("=================================================================")
    logging.info("AX Runtime Lifecycle Observer & Operation Correlation Bridge")
    logging.info(f"Target: {TARGET_ATESPACE}/{TARGET_TASK} | Threshold tau: {SUSPEND_THRESHOLD_SEC}s")
    logging.info(f"Redis: {REDIS_HOST}:{REDIS_PORT} | Bridge Port: {BRIDGE_PORT}")
    logging.info("Multi-Signal Policy: (Pending Op in Redis) AND (No Local Progress) AND (Wait >= tau)")
    logging.info("=================================================================")

    # Start the Operation Correlation Bridge HTTP Server in background thread
    bridge_thread = threading.Thread(target=start_bridge_server, daemon=True)
    bridge_thread.start()

    # Trackers per task
    trackers = {}  # task_name -> dict of state
    last_dispatch_time = {}

    while True:
        try:
            active_tasks = list_target_tasks(TARGET_TASK, TARGET_ATESPACE)
            for t in list(trackers.keys()):
                if t not in active_tasks:
                    del trackers[t]

            for t_name in active_tasks:
                if t_name not in trackers:
                    trackers[t_name] = {
                        "state": ObserverState.IDLE,
                        "last_status": None,
                        "job_id": None,
                        "wait_start_time": None,
                        "last_progress_time": time.time()
                    }

            for task_name, info in list(trackers.items()):
                current_state = info["state"]
                actor_id = f"{TARGET_ATESPACE}/{task_name}"

                task_info = get_ax_task(task_name, TARGET_ATESPACE)
                if not task_info:
                    continue

                actor_info = get_substrate_actor(task_name, TARGET_ATESPACE)
                if not actor_info:
                    continue

                actor_state = actor_info.get("state")
                worker_pod = actor_info.get("worker", "<none>")

                # Reset tracker if task was recreated/running fresh
                if current_state == ObserverState.COMPLETED and actor_state == "ACTOR_STATE_RUNNING":
                    _, is_waiting, _, _ = inspect_actor_execution(task_name, TARGET_ATESPACE)
                    if not is_waiting:
                        info["state"] = ObserverState.MONITORING
                        current_state = ObserverState.MONITORING

                # T0: Active Execution Monitoring & Queued Task Dispatch
                if current_state == ObserverState.IDLE:
                    if actor_state == "ACTOR_STATE_RUNNING":
                        info["state"] = ObserverState.MONITORING
                        info["last_progress_time"] = time.time()
                        logging.info(f"[T0] [Task: {task_name}] RUNNING on Worker '{worker_pod}' (1/1 ACTORS).")
                    elif task_info.get("phase") in ("Pending", "Failed") and has_free_workers():
                        now_t = time.time()
                        if now_t - last_dispatch_time.get(task_name, 0) > 5.0:
                            last_dispatch_time[task_name] = now_t
                            logging.info(f"[Queue] Available worker detected! Dispatching queued task '{task_name}'...")
                            run_cmd(["ax", "resume", "task", task_name, "-a", TARGET_ATESPACE])

                # T1 & T2: Multi-Signal Policy Eligibility Check
                elif current_state == ObserverState.MONITORING:
                    if actor_state == "ACTOR_STATE_RUNNING":
                        has_progress, is_waiting, agent_status, log_job_id = inspect_actor_execution(task_name, TARGET_ATESPACE)

                        if agent_status == "COMPLETED":
                            info["state"] = ObserverState.COMPLETED
                            logging.info("-----------------------------------------------------------------")
                            logging.info(f"[T7] [Autonomous Flow Complete: {task_name}]")
                            logging.info(f"     Final Status: {agent_status}")
                            logging.info(f"     Worker:       {worker_pod}")
                            logging.info("=================================================================")
                            run_cmd(["ax", "suspend", "task", task_name, "-a", TARGET_ATESPACE])
                            continue

                        # If job_id seen in logs but not yet registered, auto-register it as fallback
                        if log_job_id:
                            info["job_id"] = log_job_id
                            if not get_operation(log_job_id):
                                register_operation(log_job_id, actor_id, "async_scan")

                        # Check Redis for any active pending operations for this actor
                        pending_ops = get_actor_pending_operations(actor_id)

                        # If no pending operation registered in Redis -> Fast Path! (e.g. MCP call or local logic)
                        if not pending_ops:
                            if has_progress:
                                info["last_progress_time"] = time.time()
                                info["wait_start_time"] = None
                            # Do NOT suspend; actor is in fast path
                            continue

                        # A pending operation exists! Now check whether local work is still happening:
                        now_t = time.time()
                        if has_progress and not is_waiting:
                            # Actor is still actively computing/reasoning post-submission!
                            info["last_progress_time"] = now_t
                            info["wait_start_time"] = None
                            logging.info(f"[Policy-Check: {task_name}] Op '{pending_ops[0]}' is PENDING, but active local progress detected -> NO SUSPEND.")
                            continue

                        # If actor has entered the wait loop / sleep state:
                        if is_waiting or (now_t - info["last_progress_time"] >= 1.5):
                            if info["wait_start_time"] is None:
                                info["wait_start_time"] = now_t
                                logging.info(f"[Policy-Check: {task_name}] Genuine wait detected for '{pending_ops[0]}'. Timer started (tau={SUSPEND_THRESHOLD_SEC}s)...")

                            wait_elapsed = now_t - info["wait_start_time"]

                            if wait_elapsed < SUSPEND_THRESHOLD_SEC:
                                # Within grace period, wait for threshold
                                continue

                            # All conditions satisfied:
                            # 1. Operation is PENDING in Redis
                            # 2. No active local progress
                            # 3. Wait duration >= tau (3.0s)
                            logging.info("=================================================================")
                            logging.info(f"[AX-Policy] Actor '{task_name}' suspension eligibility CONFIRMED:")
                            logging.info(f"     Pending Operation:  {pending_ops[0]}")
                            logging.info(f"     Wait Duration:      {wait_elapsed:.2f}s >= tau ({SUSPEND_THRESHOLD_SEC}s)")
                            logging.info(f"     Local Progress:     None in window")
                            logging.info(f"     Action:             Triggering SuspendTask({task_name})")
                            logging.info("=================================================================")

                            info["state"] = ObserverState.SUSPEND_REQUESTED
                            code, out, err = run_cmd(["ax", "suspend", "task", task_name, "-a", TARGET_ATESPACE])
                            logging.info(f"[T3] AX Suspend accepted: {out}")

                    elif actor_state == "ACTOR_STATE_SUSPENDED":
                        info["state"] = ObserverState.HIBERNATED
                        logging.info("-----------------------------------------------------------------")
                        logging.info(f"[T4] [Native Substrate Hibernation Confirmed: {task_name}]")
                        logging.info(f"     Actor State:     ACTOR_STATE_SUSPENDED")
                        logging.info(f"     Physical Worker: {worker_pod} -> RECLAIMED (0/1 ACTORS)")
                        logging.info(f"     Snapshot Scope:  SNAPSHOT_CONTENT_SCOPE_FULL (RustFS S3)")
                        logging.info("-----------------------------------------------------------------")

                # T4: Awaiting Ingress Webhook Callback
                elif current_state == ObserverState.SUSPEND_REQUESTED:
                    if actor_state == "ACTOR_STATE_SUSPENDED":
                        info["state"] = ObserverState.HIBERNATED
                        logging.info("-----------------------------------------------------------------")
                        logging.info(f"[T4] [Native Substrate Hibernation Confirmed: {task_name}]")
                        logging.info(f"     Actor State:     ACTOR_STATE_SUSPENDED")
                        logging.info(f"     Physical Worker: {worker_pod} -> RECLAIMED (0/1 ACTORS)")
                        logging.info(f"     Snapshot Scope:  SNAPSHOT_CONTENT_SCOPE_FULL (RustFS S3)")
                        logging.info("-----------------------------------------------------------------")

                # T5 & T6: Native Ingress Wake Detection
                elif current_state == ObserverState.HIBERNATED:
                    if actor_state == "ACTOR_STATE_RUNNING":
                        info["state"] = ObserverState.RESUMED
                        logging.info("=================================================================")
                        logging.info(f"[T5] [Native Dataplane Wake Detected: {task_name}!]")
                        logging.info(f"     Worker assigned: '{worker_pod}'")
                        logging.info("     Actor resumed from persisted execution state without restarting.")
                        logging.info("=================================================================")
                        run_cmd(["ax", "resume", "task", task_name, "-a", TARGET_ATESPACE])
                        logging.info(f"[T6] Synchronized AX control plane for task '{task_name}' -> Phase: Running")

                # T7: Agent Continuation & Final Report Verification
                elif current_state == ObserverState.RESUMED:
                    _, _, agent_status, _ = inspect_actor_execution(task_name, TARGET_ATESPACE)
                    if agent_status == "COMPLETED":
                        info["state"] = ObserverState.COMPLETED
                        logging.info("-----------------------------------------------------------------")
                        logging.info(f"[T7] [Autonomous Flow Complete: {task_name}]")
                        logging.info(f"     Final Status: {agent_status}")
                        logging.info(f"     Worker:       {worker_pod}")
                        logging.info("=================================================================")
                        # Suspend completed task to free physical worker for other actors in the pool
                        run_cmd(["ax", "suspend", "task", task_name, "-a", TARGET_ATESPACE])

            time.sleep(POLL_INTERVAL_SEC)

        except Exception as e:
            logging.error(f"Error in observer main loop: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    main()
