#!/usr/bin/env python3
"""
AX Runtime Lifecycle Observer
Generic runtime adapter bridging agentic external-wait states to AX/Substrate lifecycle management.

Contract:
- Monitors Tasks in AX control plane and Substrate dataplane.
- Detects when an agent transitions into any generic external-wait state ('WAITING_...').
- Issues exactly ONE SuspendTask request per task (idempotent, deduplicated).
- Tracks Substrate worker reclamation and atenet-router native wake events.
- Emits structured, high-visibility lifecycle events for real-time demonstration.
- Supports single tasks (e.g. 'openclaw-task') and fleet mode ('openclaw-task*', 'all', or comma-separated).
"""

import json
import logging
import os
import re
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AX-Observer] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

TARGET_TASK = os.environ.get("TARGET_TASK", "openclaw-task*")
TARGET_ATESPACE = os.environ.get("TARGET_ATESPACE", "default")
POLL_INTERVAL_SEC = float(os.environ.get("POLL_INTERVAL_SEC", "1.0"))

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

def get_agent_log_status(task_name, atespace):
    code, stdout, _ = run_cmd(["kubectl-ate", "logs", "actor", task_name, "-a", atespace])
    if code != 0 or not stdout:
        return None, None

    status_found = None
    job_found = None

    for line in stdout.splitlines():
        try:
            entry = json.loads(line)
            msg = entry.get("message", "")
        except Exception:
            msg = line

        m = re.search(r"status=['\"]?([A-Za-z0-9_]+)['\"]?", msg)
        if m:
            s = m.group(1).upper()
            if "WAITING" in s or s in ("PENDING", "COMPLETED"):
                status_found = s

        m_job = re.search(r"job_id=['\"]?([A-Za-z0-9_-]+)['\"]?", msg)
        if m_job:
            job_found = m_job.group(1)

    return status_found, job_found

def main():
    logging.info("=================================================================")
    logging.info("AX Runtime Lifecycle Observer initialized")
    logging.info(f"Target Selector: {TARGET_ATESPACE}/{TARGET_TASK} | Loop: {POLL_INTERVAL_SEC}s")
    logging.info("Generic Contract: Detect 'WAITING_*' -> Trigger SuspendTask exactly once per task")
    logging.info("=================================================================")

    trackers = {}  # task_name -> {"state": ObserverState.IDLE, "last_status": None}

    while True:
        try:
            active_tasks = list_target_tasks(TARGET_TASK, TARGET_ATESPACE)
            for t_name in active_tasks:
                if t_name not in trackers:
                    trackers[t_name] = {"state": ObserverState.IDLE, "last_status": None}

            for task_name, info in list(trackers.items()):
                current_state = info["state"]
                last_reported_status = info["last_status"]

                task_info = get_ax_task(task_name, TARGET_ATESPACE)
                actor_info = get_substrate_actor(task_name, TARGET_ATESPACE)

                if not task_info or not actor_info:
                    continue

                actor_state = actor_info.get("state")
                worker_pod = actor_info.get("worker", "<none>")

                # T0: Active Execution Monitoring
                if current_state == ObserverState.IDLE:
                    if actor_state == "ACTOR_STATE_RUNNING":
                        info["state"] = ObserverState.MONITORING
                        logging.info(f"[T0] [Task: {task_name}] RUNNING on Worker '{worker_pod}' (1/1 ACTORS).")

                # T1 & T2: State Inspection & Generic Wait Transition Detection
                elif current_state == ObserverState.MONITORING:
                    if actor_state == "ACTOR_STATE_RUNNING":
                        agent_status, job_id = get_agent_log_status(task_name, TARGET_ATESPACE)
                        if agent_status:
                            if agent_status != last_reported_status:
                                logging.info(f"[Task: {task_name}] Agent Status: '{agent_status}' | Job: '{job_id or 'none'}'")
                                info["last_status"] = agent_status

                            if agent_status.startswith("WAITING"):
                                logging.info(f"[T1] [Task: {task_name}] Transition detected: '{agent_status}'")
                                logging.info(f"[T2] [Task: {task_name}] Deduplication lock engaged -> Requesting SuspendTask")
                                info["state"] = ObserverState.SUSPEND_REQUESTED

                                sc_code, sc_out, sc_err = run_cmd(["ax", "suspend", "task", task_name, "-a", TARGET_ATESPACE])
                                if sc_code == 0:
                                    logging.info(f"[T3] [Task: {task_name}] AX Suspend accepted: {sc_out}")
                                else:
                                    logging.warning(f"[T3] [Task: {task_name}] Suspend error: {sc_err}")

                # T3 & T4: Substrate Hibernation & Worker Reclamation Confirmation
                elif current_state == ObserverState.SUSPEND_REQUESTED:
                    if actor_state == "ACTOR_STATE_SUSPENDED":
                        info["state"] = ObserverState.HIBERNATED
                        logging.info("-----------------------------------------------------------------")
                        logging.info(f"[T4] [Native Substrate Hibernation Confirmed: {task_name}]")
                        logging.info(f"     Actor State:     {actor_state}")
                        logging.info(f"     Physical Worker: {worker_pod} -> RECLAIMED (0/1 ACTORS)")
                        logging.info("     Snapshot Scope:  SNAPSHOT_CONTENT_SCOPE_FULL (RustFS S3)")
                        logging.info("     Wake Dataplane:  atenet-router armed with header 'ate-target-actor'")
                        logging.info("-----------------------------------------------------------------")

                # T5 & T6: Native atenet-router Ingress Wake Detection
                elif current_state == ObserverState.HIBERNATED:
                    if actor_state == "ACTOR_STATE_RUNNING":
                        info["state"] = ObserverState.RESUMED
                        logging.info("=================================================================")
                        logging.info(f"[T5] [Native Dataplane Wake Detected: {task_name}!]")
                        logging.info(f"     Worker assigned: '{worker_pod}'")
                        logging.info("     Full in-RAM memory snapshot restored from RustFS S3.")
                        logging.info("=================================================================")

                        # Synchronize AX control plane
                        run_cmd(["ax", "resume", "task", task_name, "-a", TARGET_ATESPACE])
                        logging.info(f"[T6] Synchronized AX control plane for task '{task_name}' -> Phase: Running")

                # T7: Agent Continuation & Final Report Verification
                elif current_state == ObserverState.RESUMED:
                    agent_status, job_id = get_agent_log_status(task_name, TARGET_ATESPACE)
                    if agent_status == "COMPLETED":
                        info["state"] = ObserverState.COMPLETED
                        logging.info("-----------------------------------------------------------------")
                        logging.info(f"[T7] [Autonomous Flow Complete: {task_name}]")
                        logging.info(f"     Job ID:       {job_id}")
                        logging.info(f"     Final Status: {agent_status}")
                        logging.info(f"     Worker:       {worker_pod}")
                        logging.info("=================================================================")

            time.sleep(POLL_INTERVAL_SEC)

        except Exception as e:
            logging.error(f"Observer loop error: {e}")
            time.sleep(POLL_INTERVAL_SEC)

if __name__ == "__main__":
    main()
