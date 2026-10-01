# Phase 1 & Phase 2: Autonomous Actor Lifecycle & Correlation Bridge Verification Report

## 1. Executive Summary & Architectural Validation

This report documents the end-to-end verification of the **Google AX on Agent Substrate (ATE)** autonomous actor lifecycle, validating non-invasive actor multiplexing driven by external operation correlation and runtime state observation.

### Headline Architectural Result
The complete autonomous lifecycle was verified under strict architectural boundaries without modifying OpenClaw agent application code:
```
external operation → correlation → observed wait → suspension → worker reclamation → asynchronous completion → correlation → native resume → continued execution
```

```
┌─────────────────┐       1. Call Fast MCP (~100ms)        ┌─────────────────────────┐
│                 ├───────────────────────────────────────►│  External MCP Server    │
│                 │◄───────────────────────────────────────┤  (lookup_threat_intel)  │
│                 │       Fast return (< tau=3s)           └─────────────────────────┘
│                 │       [Worker Retained: 1/1]
│                 │
│                 │       2. Async Call (202 Accepted)     ┌─────────────────────────┐
│    OpenClaw     ├───────────────────────────────────────►│    External Async API   │
│   (Unmodified)  │◄───────────────────────────────────────┤ (e.g. Vuln Scanner)     │
│                 │       operation_id: "ext-scan-001"     └────────────┬────────────┘
│                 │                                                     │
│                 │  3. Local Reasoning (~2s)                           │
│                 │     [No Suspend - Progress Detected]                │ Registers op
│                 │                                                     ▼
│                 │  4. Enters Wait Loop (wait >= 3s)      ┌─────────────────────────┐
│                 │                                        │  Operation Registry &   │
└────────┬────────┘                                        │   Correlation Bridge    │
         │                                                 │   (operations:ext-scan) │
         │ Observed State:                                 └────────────▲────────────┘
         │  • Pending Op in Redis                                       │
         │  • No Local Progress                                         │ 5. Webhook:
         │  • Wait >= tau (3.0s)                                        │    {operation_id}
         ▼                                                              │    (No Actor Header)
┌─────────────────┐                                                     │
│   AX Runtime    ├─────────────────┐                                   │
│    Observer     │ 4b. SuspendTask │                                   │
└─────────────────┘                 ▼                                   │
                           ┌─────────────────┐                          │
                           │  AX / Substrate ├────────┐                 │
                           │    Controller   │        │ Snapshot        │
                           └─────────────────┘        ▼ (RustFS S3)     │
                                             ┌─────────────────┐        │
                                             │ Physical Worker │        │
                                             │    RECLAIMED    │        │
                                             │   (0/1 ACTORS)  │        │
                                             └─────────────────┘        │
                                                                        │
                                   7. Target HTTP 404 (Routing)         │
                                      & Native ResumeActor()            │
                                   ┌────────────────────────────────────┴───────────────┐
                                   │ 6. Correlation Bridge resolves actor, injects      │
                                   │    Resolved Actor Envelope (ate-target-actor)     │
                                   │    and forwards to atenet-router                   │
                                   └────────────────────────────────────┬───────────────┘
                                                                        │
                                                                        ▼
                                                           ┌─────────────────────────┐
                                                           │   Substrate Restores    │
                                                           │ Actor onto Pool Worker  │
                                                           │   (1/1 ACTORS, S3)      │
                                                           └────────────┬────────────┘
                                                                        │
                                                                        ▼
                                                           ┌─────────────────────────┐
                                                           │    OpenClaw Resumes     │
                                                           │  from Persisted State   │
                                                           │  & Writes report.md     │
                                                           └─────────────────────────┘
```

---

## 2. Experimental Verification Matrix: The 4 Distinct Paths

Four distinct runtime execution paths were independently evaluated and verified against the configured threshold $\tau = 3.0\text{s}$:

| Path | Execution Description | External Latency / Local Duration | Multi-Signal Observer Evaluation | Observed Action & Worker Status | Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Path 1** | **Fast MCP Tool Call** (`lookup_threat_intel`) | $\sim 100 - 150\text{ms}$ ($< \tau$) | Fast tool return. No pending long-running async operation in Redis. | **No suspension**. Actor remains in `ACTOR_STATE_RUNNING`. Physical worker retained (`1/1` ACTORS). | **PASSED** |
| **Path 2** | **Post-Submission Local Progress** | $\sim 2.0\text{s}$ active local reasoning | Operation registered as `PENDING`, but active local reasoning signals emitted (`[Local Progress]`). | **No suspension**. Observer policy inhibits suspension while local progress is occurring. Worker retained (`1/1`). | **PASSED** |
| **Path 3** | **Genuine Wait Expiration & Suspension** | Wait duration $\ge 3.0\text{s}$ (measured $3.60\text{s}$) | Operation is `PENDING`, no active progress detected, idle duration exceeds $\tau=3.0\text{s}$. | **Suspension Triggered**. `ax suspend task openclaw-task` executed. Full memory checkpoint to RustFS S3. **Worker compute capacity was reclaimed during the external wait** (`0/1` ACTORS). | **PASSED** |
| **Path 4** | **Decoupled Ingress Webhook & Dynamic Resumption** | Async job completes ($\sim 20\text{s}$); Webhook dispatched | Ingress callback received containing only `operation_id`. Bridge resolves actor identity. | **Native Dataplane Resume**. Snapshot restored onto available pool worker. Actor resumes from persisted execution state without restarting workflow. Generates `/workspace/report.md`. | **PASSED** |

---

## 3. Detailed Trace & Observable Evidence

### 3.1 Path 1: Fast MCP Verification (Zero-Suspension Guarantee)
- **Target:** External MCP Server (`http://external-mcp-server:8000/tools/lookup_threat_intel`)
- **Payload:** `{"ip": "198.51.100.23"}`
- **Observed Trace Logs:**
  ```text
  [OpenClaw] Step 1: Querying Threat Intel MCP for IP 198.51.100.23...
  [OpenClaw] [MCP Call] Invoking external tool lookup_threat_intel...
  [OpenClaw] Threat Intel response received in 112ms: {"risk_score": 85, "classification": "malicious"}
  ```
- **Observer Verification:**
  - Fast call completed in $112\text{ms} < \tau$ ($3.0\text{s}$).
  - No long-running operation registered in Redis.
  - Substrate actor status remained `ACTOR_STATE_RUNNING` on worker pod `worker-pool-5cd675b8c4-c27sk`.
  - Worker pool occupancy remained `1/1 ACTORS`.

### 3.2 Path 2: Post-Submission Local Reasoning Verification
- **Target:** Async Vulnerability Scanner (`http://external-scan-api:8080/api/v1/scans`)
- **HTTP Response:** `202 Accepted` with payload:
  ```json
  {
    "status": "PENDING",
    "operation_id": "ext-scan-001",
    "target": "security-target.default.svc.cluster.local",
    "eta_seconds": 20
  }
  ```
- **Registration in Redis:**
  ```text
  HSET operations:ext-scan-001 operation_id "ext-scan-001" actor "default/openclaw-task" status "PENDING"
  SADD actor_pending_ops:default/openclaw-task "ext-scan-001"
  ```
- **Actor Local Progress Trace:**
  ```text
  [OpenClaw] Async scan submitted! Operation ID: ext-scan-001. ETA: 20s.
  [OpenClaw] Executing local reasoning and preprocessing before entering wait state...
  [OpenClaw] [Local Progress] Parsing target domain structure...
  [OpenClaw] [Local Progress] Cross-referencing threat intelligence feed...
  [OpenClaw] [Local Progress] Initializing correlation matrix...
  [OpenClaw] Local analysis complete.
  ```
- **Observer Decision:**
  ```text
  [Policy-Check: openclaw-task] Op 'ext-scan-001' is PENDING, but active local progress detected -> NO SUSPEND.
  ```
  **Confirmed:** Even with an active pending operation in Redis, the observer inhibits suspension as long as local progress is actively occurring.

### 3.3 Path 3: Genuine Wait Expiration & Worker Reclamation
- **Actor Wait State:** OpenClaw enters wait loop after completing local work:
  ```text
  [OpenClaw] Entering waiting state for scan ext-scan-001 completion...
  [OpenClaw] [Wait Loop] Polling check 1 for operation ext-scan-001...
  ```
- **Observer Timer & Policy Decision:**
  ```text
  [Policy-Check: openclaw-task] Genuine wait detected for 'ext-scan-001'. Timer started (tau=3.0s)...
  =================================================================
  [AX-Policy] Actor 'openclaw-task' suspension eligibility CONFIRMED:
       Pending Operation:  ext-scan-001
       Wait Duration:      3.60s >= tau (3.0s)
       Local Progress:     None in window
       Action:             Triggering SuspendTask(openclaw-task)
  =================================================================
  ```
- **Substrate Worker Reclamation & Durable Snapshot:**
  ```text
  -----------------------------------------------------------------
  [T4] [Native Substrate Hibernation Confirmed: openclaw-task]
       Actor State:     ACTOR_STATE_SUSPENDED
       Physical Worker: worker-pool-5cd675b8c4-c27sk -> RECLAIMED (0/1 ACTORS)
       Snapshot Scope:  SNAPSHOT_CONTENT_SCOPE_FULL (RustFS S3)
  -----------------------------------------------------------------
  ```
- **Worker Allocation Status During Wait:**
  ```text
  NAME                                   POOL          STATE                 ACTORS   POD
  acc8c21c-b671-4a4d-a0da-1497a040a143   worker-pool   WORKER_STATE_ACTIVE   0/1      worker-pool-5cd675b8c4-c27sk
  b097eb27-d25c-497e-8b12-4a958b25b7ab   worker-pool   WORKER_STATE_ACTIVE   0/1      worker-pool-5cd675b8c4-gf9nl
  6614b5ce-1707-4c14-b77d-0034b721e288   worker-pool   WORKER_STATE_ACTIVE   0/1      worker-pool-5cd675b8c4-snczn
  ```
  **Confirmed:** Worker compute capacity was reclaimed during the external wait across all 3 physical pool workers.

### 3.4 Path 4: Decoupled Webhook Ingress & Dynamic Resumption
- **Asynchronous Completion Callback:** External Scan API completes background job after 20s and dispatches callback to `http://kind-control-plane:30088/webhook`:
  ```json
  {
    "operation_id": "ext-scan-001",
    "status": "COMPLETED"
  }
  ```
  *(Notice: The external system has zero awareness of Kubernetes, AX, or actor identities; no internal headers are provided by the caller).*
- **Operation Correlation Bridge Execution:**
  ```text
  [Bridge-Webhook] Incoming completion callback for operation 'ext-scan-001'
  [Bridge-Webhook] Operation 'ext-scan-001' atomically marked COMPLETED. Target Actor: 'default/openclaw-task'
  [Bridge-Webhook] Resolved actor envelope: Injecting header 'ate-target-actor: default/openclaw-task' -> http://atenet-router.ate-system.svc.cluster.local:8080
  [Bridge-Webhook] atenet-router dispatched resume for 'default/openclaw-task' (Target HTTP 404)
  ```

#### Architectural Note on "Target HTTP 404":
The `Target HTTP 404` logged above represents the HTTP response from the routing endpoint separate from the successful native resume operation. In Substrate's dataplane architecture:
1. `atenet-router` receives the packet with `ate-target-actor: default/openclaw-task`.
2. Because the actor is in `ACTOR_STATE_SUSPENDED`, `atenet-router` initiates `ResumeActor()` to restore the actor.
3. The router then attempts to proxy the raw HTTP request to the actor's port. Because the OpenClaw agent is a CLI/worker process that does not listen on an HTTP port at `/webhook`, the proxied connection returns an HTTP 404 error from the target application layer.
4. Crucially, the resume operation has already succeeded at the Substrate dataplane layer, and the actor is successfully awakened.

- **Dynamic Worker Migration & Resumption:**
  ```text
  =================================================================
  [T5] [Native Dataplane Wake Detected: openclaw-task!]
       Worker assigned: 'ax-system/worker-pool-5cd675b8c4-gf9nl'
       Actor resumed from persisted execution state without restarting.
  =================================================================
  [T6] Synchronized AX control plane for task 'openclaw-task' -> Phase: Running
  ```
- **Continued Execution & Report Generation:**
  ```text
  [OpenClaw] Resumed from wait loop! Checking scan status...
  [OpenClaw] Result retrieved: 3 vulnerabilities identified.
  [OpenClaw] Generated final report at /workspace/report.md
  [OpenClaw] Workflow complete. Final status: COMPLETED
  ```

---

## 4. Key Architectural Guarantees Established

1. **Strict Decoupling of Actor Identity:**
   External tools and asynchronous APIs interact solely with standard identifiers (`operation_id`). The Operation Correlation Bridge isolates internal routing metadata (`ate-target-actor: default/openclaw-task`) within the cluster.
2. **Atomic Duplicate Callback Suppression:**
   A duplicate completion callback for `ext-scan-001` was sent to the Bridge. Redis transaction returned `ALREADY_COMPLETED`, yielding:
   ```json
   {"status": "ALREADY_COMPLETED", "message": "Duplicate callback suppressed"}
   ```
   No duplicate `ResumeActor()` calls or workflow restarts occurred.
3. **Execution State Continuity:**
   The actor did not re-run initialization, did not re-query the Threat Intel MCP, and did not re-submit a duplicate external scan. Execution resumed from its persisted execution state directly inside the polling/wait loop.
4. **Physical Worker Reusability:**
   During the ~20s external scan wait, the physical worker pod was reclaimed and made available for other workloads. Worker migration was demonstrated as the actor was suspended from `worker-pool-5cd675b8c4-c27sk` and restored onto `worker-pool-5cd675b8c4-gf9nl`.
