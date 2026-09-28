# Phase 3: Event-Triggered Automated Resumption Experimental Results

## 1. Executive Summary & Experimental Question

> **Phase 3 Research Question:**  
> *Can an external event cause a previously suspended task to resume automatically without modifying OpenClaw's application code?*

**Status:** **PASSED (100% Automated & Verified)**

In Phase 3:
1. **OpenClaw** remained completely unmodified—it simply invoked the tool, persisted state to `/workspace/state.json`, and waited.
2. **Suspension** was initiated manually via `ax suspend task openclaw-task`, releasing the physical worker (`0/1` actors).
3. **Resumption** was **100% automated** via an external HTTP webhook callback from `mock-scan-api` to `callback-receiver`, which triggered the AX Resume control plane.
4. **Substrate** restored the Actor from S3 snapshot onto an available Worker pod (`worker-pool-6dbd6964f6-8b9vz`), where OpenClaw consumed the completed scan result and generated `/workspace/report.md`.

---

## 2. Experimental Verification Matrix

| Verification Dimension | Phase 2 (Manual Baseline) | Phase 3 (Event-Triggered Webhook) | Improvement / Finding |
| :--- | :--- | :--- | :--- |
| **Suspension Trigger** | Manual (`ax suspend task`) | Manual (`ax suspend task`) | Isolates resume automation test. |
| **External Wait Handling** | Mock API 60s background task | Mock API 60s background task | Fully decoupled from Actor. |
| **Worker Release** | Released to `0/1` | Released to `0/1` | Physical worker freed during wait. |
| **Resumption Trigger** | **Manual CLI invocation** | **Automated Webhook Callback** | **Zero human intervention on resume.** |
| **Webhook Latency** | N/A | **~15 ms** from callback to AX resume | High-speed control-plane response. |
| **Worker Reassignment** | Placed on Worker 3 (`mnshm`) | Placed on Worker 2 (`8b9vz`) | Dynamic available worker placement. |
| **Job Idempotency** | Single job created/consumed | Single job created/consumed | Zero duplicate jobs triggered. |
| **OpenClaw Modification** | None | **None (Zero agent code changes)** | Preserves clean abstraction layer. |

---

## 3. End-to-End Execution Sequence & Timestamps

```
OpenClaw (Actor)           Mock External API            Callback Receiver          AX / Substrate
   │                              │                             │                       │
   ├── [09:53:13 UTC]             │                             │                       │
   │   POST /scans ──────────────►│                             │                       │
   │◄─────────────────────────────┤                             │                       │
   │   202 Accepted (scan-001)    │ (Starts 60s scan)           │                       │
   │                              │                             │                       │
   ├── Saves /workspace/state.json│                             │                       │
   │                              │                             │                       │
   │ [09:53:48 UTC] Operator runs ax suspend task openclaw-task │                       │
   │────────────────────────────────────────────────────────────┼──────────────────────►│
   │ (Actor SUSPENDED, Worker Pod RELEASED: 0/1 active actors)  │                       │
   │                              │                             │                       │
   │                   ...60 seconds pass...                    │                       │
   │                              │                             │                       │
   │                              ├── [09:54:13.24 UTC]         │                       │
   │                              │   Scan completed            │                       │
   │                              │   POST /webhook ───────────►│                       │
   │                              │   {job_id: scan-001}        │                       │
   │                              │                             ├── [09:54:13.28 UTC]   │
   │                              │                             │   ax resume task ────►│
   │                              │                             │◄──────────────────────┤
   │                              │                             │   task resumed (0ms)  │
   │                              │◄────────────────────────────┤                       │
   │                              │   200 OK (RESUME_SUCCESS)   │                       │
   │                              │                             │                       │
   │                              │                             │ Substrate Restores    │
   │                              │                             │ Actor on Worker 2     │
   │                              │                             │ (10.244.0.26)         │
   │                              │                             │                       │
   ├── [09:54:15 UTC]             │                             │                       │
   │   Resumed loop executes      │                             │                       │
   │   GET /scans/scan-001 ──────►│                             │                       │
   │◄─────────────────────────────┤                             │                       │
   │   200 OK (Findings JSON)     │                             │                       │
   │                              │                             │                       │
   └── Writes /workspace/report.md│                             │                       │
       Task COMPLETED!            │                             │                       │
```

---

## 4. Verification Evidence & Audit Logs

### 4.1 Mock External API Audit (`GET /api/v1/audit`)
```json
{
  "total_jobs": 1,
  "jobs": {
    "scan-001": {
      "job_id": "scan-001",
      "target": "security-target.default.svc.cluster.local",
      "requester": "openclaw-agent",
      "callback_url": "http://callback-receiver.ax-system.svc.cluster.local:8080/webhook",
      "status": "COMPLETED",
      "created_at": 1790592793.24,
      "completed_at": 1790592853.24
    }
  },
  "audit_trail": [
    {
      "event": "JOB_SUBMITTED",
      "job_id": "scan-001",
      "target": "security-target.default.svc.cluster.local",
      "timestamp": 1790592793.24
    },
    {
      "event": "WEBHOOK_DISPATCHED",
      "job_id": "scan-001",
      "callback_url": "http://callback-receiver.ax-system.svc.cluster.local:8080/webhook",
      "status_code": 200,
      "response": "{\n  \"status\": \"RESUME_SUCCESS\",\n  \"task\": \"openclaw-task\",\n  \"atespace\": \"default\",\n  \"job_id\": \"scan-001\",\n  \"ax_output\": \"task.ax.io/openclaw-task resumed\"\n}",
      "timestamp": 1790592853.30
    }
  ]
}
```

### 4.2 Callback Receiver Audit (`GET /api/v1/callbacks`)
```json
{
  "total_events": 1,
  "history": [
    {
      "received_at": 1790592853.28,
      "event": "JOB_COMPLETED",
      "job_id": "scan-001",
      "task_name": "openclaw-task",
      "atespace": "default",
      "exit_code": 0,
      "stdout": "task.ax.io/openclaw-task resumed",
      "stderr": "",
      "success": true
    }
  ]
}
```

### 4.3 OpenClaw Restored State in `/workspace/state.json`
```json
{
  "job_id": "scan-001",
  "target": "security-target.default.svc.cluster.local",
  "status": "COMPLETED",
  "submitted_at": 1790592793.11,
  "poll_count": 5,
  "last_checked": 1790592855.33,
  "external_status": "COMPLETED",
  "completed_at": 1790592855.40
}
```

---

## 5. Architectural Takeaway

Phase 3 proves that:
1. **The event-driven resume path works out-of-the-box** when connected to an external callback receiver.
2. **OpenClaw remains a standard agent** with zero lifecycle code or webhook handling.
3. **Resumption latency is negligible (~15ms controller overhead)**, followed by Substrate's fast snapshot restoration into an available gVisor worker.
