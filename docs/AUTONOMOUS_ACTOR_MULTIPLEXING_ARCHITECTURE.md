# Autonomous Actor Multiplexing Architecture
## Google AX + Agent Substrate (ATE) with Unmodified OpenClaw & Multi-Tool Tooling

---

## 1. Executive Summary & Problem Formulation

### 1.1 The Agentic Compute Bottleneck
Autonomous AI agents frequently execute long-running, high-latency workflows: external vulnerability scans, batch data processing, third-party API queries, or human-in-the-loop approvals. These external operations routinely require 30 seconds to several hours to complete.

In conventional Kubernetes architectures, an agent executing in a standard Pod consumes CPU, memory, and expensive compute reservations for the entire duration of the wait. Scaling to hundreds of concurrent agents rapidly exhausts cluster capacity, leading to severe resource wastage and high cloud costs.

### 1.2 The Architectural Solution
This platform combines **Google AX** (declarative agent task orchestrator) and **Agent Substrate** (hardware-virtualized, snapshot-capable actor runtime) to achieve **autonomous resource multiplexing** ($M$ logical actors scheduled across $N$ physical workers, where $M > N$):

1. **Pure Agent Decoupling:** The agent workload ([`openclaw_agent.py`](file:///home/berrybytes/Office/AX_Substrtate/openclaw/agent/openclaw_agent.py)) remains **100% unmodified** with zero awareness of Kubernetes, Substrate, or checkpointing logic.
2. **Multi-Tool Autonomous Execution:** OpenClaw executes local discovery tools (`nmap`), external threat intelligence tools over Model Context Protocol (MCP via JSON-RPC 2.0), and external asynchronous scanning APIs.
3. **Full In-Memory Process Virtualization:** During external wait states, Substrate checkpoints the entire Linux process tree and memory pages (`SNAPSHOT_CONTENT_SCOPE_FULL`) via gVisor (`runsc`) into S3 storage in **~286 milliseconds**.
4. **Physical Worker Reclamation:** The physical worker Pod is completely released back to the scheduling pool (`0/1 ACTORS`), allowing other waiting tasks to execute on the reclaimed hardware.
5. **Native Ingress Dataplane Resumption:** Upon task completion, the external system dispatches an HTTP callback through the native `atenet-router` ingress gateway. Envoy's `ext_proc` filter intercepts the `ate-target-actor` header, natively triggers Substrate's `ResumeActor` gRPC API, restores the full in-memory process onto an available worker, and the agent continues executing its loop from the exact point it suspended.

---

## 2. High-Level Architectural Topology

```
                                  EXTERNAL SERVICES (Docker / KIND Network: 172.18.0.0/16)
                                  ┌─────────────────────────────────────────────────────────┐
                                  │  external-mcp-server:8000                               │
                                  │  • MCP JSON-RPC 2.0 (lookup_threat_intel)              │
                                  └─────────────────────────────────────────────────────────┘
                                  ┌─────────────────────────────────────────────────────────┐
                                  │  external-scan-api:8080                                 │
                                  │  • 202 Accepted + 45s background vulnerability scan     │
                                  │  • Completion webhook to atenet-router:30080            │
                                  └────────────────────────┬────────────────────────────────┘
                                                           │ Ingress Callback:
                                                           │ POST /webhook
                                                           │ ate-target-actor: default/openclaw-task
                                                           ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       KUBERNETES CLUSTER (KIND)                                              │
│                                                                                                              │
│  [ate-system] INGRESS DATAPLANE                                                                              │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ service/atenet-router (NodePort 30080 -> Port 8080)                                                    │  │
│  │ • Envoy Proxy + ext_proc gRPC filter (resumer.go)                                                      │  │
│  │ • Intercepts 'ate-target-actor: <atespace>/<actor>'                                                    │  │
│  │ • Calls ate-api-server ResumeActor gRPC API                                                            │  │
│  └────────────────────────────────────────────────┬───────────────────────────────────────────────────────┘  │
│                                                   │ Wakes Actor                                              │
│                                                   ▼                                                          │
│  [ax-system] CONTROL & STATE PLANE                                                                           │
│  ┌───────────────────────┐   ┌──────────────────────┐   ┌─────────────────────────────────────────────────┐  │
│  │      ax-server        │   │    ax-controller     │   │              ax-runtime-observer                │  │
│  │ • Task CRUD REST API  │◄──┤ • Syncs Tasks        │◄──┤ • Monitors agent log stream ('WAITING_*')       │  │
│  │ • Port 8080           │   │ • Full Scope Patch   │   │ • Triggers ax suspend task (deduplicated)       │  │
│  └───────────────────────┘   └──────────┬───────────┘   │ • Syncs ax resume task upon native wake         │  │
│                                         │               └─────────────────────────────────────────────────┘  │
│                                         ▼ Schedules Actor                                                    │
│  [ate-system] SUBSTRATE CORE RUNTIME                                                                         │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ ate-api-server (gRPC: 50051)                                                                           │  │
│  │ • Coordinates WorkerPool, Actor placement, Snapshot/Restore lifecycle                                  │  │
│  └──────────────────────────────────────┬─────────────────────────────────────────────────────────────────┘  │
│                                         │ Pulls / Pushes full memory snapshots                               │
│                                         ▼                                                                    │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ rustfs (S3-compatible Object Storage: Port 9000, bucket: ate-snapshots)                                │  │
│  │ • pages.img (RAM) | checkpoint.img (gVisor state) | durable-dir.tar.zstd (/workspace)                 │  │
│  └────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                                              │
│  [ax-system] PHYSICAL WORKER POOL (3 Replicas: gVisor / runsc + KVM)                                         │
│  ┌─────────────────────────────┐ ┌─────────────────────────────┐ ┌─────────────────────────────┐             │
│  │ worker-pool-2s6cd           │ │ worker-pool-8b9vz           │ │ worker-pool-mnshm           │             │
│  │ • Free / 0 Active Actors    │ │ • Free / 0 Active Actors    │ │ • Active / 1 Active Actor   │             │
│  │ • runsc sandbox runner      │ │ • runsc sandbox runner      │ │ • Restored Actor scheduled  │             │
│  └─────────────────────────────┘ └─────────────────────────────┘ └──────────────┬──────────────┘             │
│                                                                                 │ Runs Sandbox               │
│                                                                                 ▼                            │
│  [default] OPENCLAW AGENT SANDBOX (Hardware-Isolated gVisor Container)                                       │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ /agent/run_workload.sh (Runtime Orchestrator Wrapper)                                                 │  │
│  │   ├── Stage 1: nmap -sT -Pn --unprivileged security-target.default.svc.cluster.local                   │  │
│  │   ├── Stage 2: curl JSON-RPC -> external-mcp-server:8000/mcp (via atenet-egress)                      │  │
│  │   └── Stage 3: exec python3 /agent/openclaw_agent.py (Unmodified)                                      │  │
│  │                  • POST /api/v1/scans -> external-scan-api:8080 (via atenet-egress)                   │  │
│  │                  • status: WAITING_FOR_EXTERNAL_API                                                    │  │
│  │                  • Discrete polling loop: time.sleep(5)                                                │  │
│  │                  • Final report generated in /workspace/report.md                                      │  │
│  └──────────────────────────────────────┬─────────────────────────────────────────────────────────────────┘  │
│                                         │ Outbound egress                                                    │
│                                         ▼                                                                    │
│  [ate-system] EGRESS DATAPLANE                                                                               │
│  ┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ atunnel (Worker sidecar) -> atenet-egress (Envoy forward proxy: Port 443)                             │  │
│  │ • Injects & verifies SPIFFE identity: spiffe://substrate-actor.local/atespace/default/actor/<name>     │  │
│  └────────────────────────────────────────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Built-in vs. Custom Engineering Breakdown

To ensure absolute architectural clarity, the table below delineates what components are native to Google AX and Agent Substrate versus what was specifically custom-built and configured for this workload.

| Component / Subsystem | Native / Custom | Origin & Source Reference | Role & Technical Significance |
| :--- | :--- | :--- | :--- |
| **AX Task CRD & CLI** | **Native Built-in** | `ax/pkg/apis/task/v1alpha1`, `cmd/ax` | Declarative task definition (`kind: Task`), workspace persistence mapping, and CLI primitives (`ax apply`, `ax get`, `ax suspend`, `ax resume`, `ax ssh`). |
| **AX Controller Snapshot Scope** | **Patched (Custom)** | [`ax/internal/substrate/client.go:266-267`](file:///home/berrybytes/Office/AX_Substrtate/ax/internal/substrate/client.go#L266-L267) | **Critical Patch:** Upgraded `OnPause` and `OnCommit` snapshot scope from `SNAPSHOT_CONTENT_SCOPE_DATA` (disk only) to `SNAPSHOT_CONTENT_SCOPE_FULL` (full gVisor RAM memory dump). |
| **Substrate Core & WorkerPool** | **Native Built-in** | `substrate/cmd/ateapi`, `atecontroller` | Replicaset worker pool management (`WorkerPool: 3`), gVisor runsc sandbox management, S3 snapshot sync to RustFS. |
| **`atenet-router` Resumption Engine** | **Native Built-in** | `substrate/cmd/atenet/internal/router/ingress/handler.go`, `resumer.go` | Envoy `ext_proc` gRPC processor that intercepts `ate-target-actor` header, automatically calls `ResumeActor`, allocates worker, and un-parks HTTP request. |
| **`atenet-router` NodePort 30080** | **Custom Config** | Patched `service/atenet-router` in `ate-system` | Exposed internal Envoy router to external Docker containers on the `kind` network (`172.18.0.0/16`) so external webhooks can reach it. |
| **`atenet-egress` Identity Proxy** | **Native Built-in** | `substrate/cmd/atenet/internal/router/egress` | Envoy forward proxy with SPIFFE mTLS authentication verifying client identity (`spiffe://substrate-actor.local/atespace/<atespace>/actor/<name>`). |
| **OpenClaw Agent Core** | **Unmodified** | [`openclaw/agent/openclaw_agent.py`](file:///home/berrybytes/Office/AX_Substrtate/openclaw/agent/openclaw_agent.py) | **Zero Changes to Agent Logic:** Standard agent that executes HTTP scan, sets `WAITING_FOR_EXTERNAL_API`, loops with `time.sleep(5)`, and writes markdown report. |
| **Runtime Workload Wrapper** | **Custom Engineering** | [`openclaw/agent/run_workload.sh`](file:///home/berrybytes/Office/AX_Substrtate/openclaw/agent/run_workload.sh) | Container entrypoint that chains local discovery (`nmap`), external threat intel query (MCP over egress), and unmodified OpenClaw agent execution. |
| **External MCP Intel Server** | **Custom Service** | [`external-services/mcp-server/server.py`](file:///home/berrybytes/Office/AX_Substrtate/external-services/mcp-server/server.py) | Standalone JSON-RPC 2.0 server running outside Kubernetes on Docker network `kind` (`172.18.0.4:8000`), serving threat intel tools (`lookup_threat_intel`). |
| **External Async Scan API** | **Custom Service** | [`external-services/scan-api/main.py`](file:///home/berrybytes/Office/AX_Substrtate/external-services/scan-api/main.py) | Standalone service running outside Kubernetes on Docker network `kind` (`172.18.0.5:8080`), implementing 202 Accepted, 45s timer, and webhook callback with `ate-target-actor`. |
| **AX Runtime Lifecycle Observer** | **Custom Integration** | [`ax-runtime-observer/observer.py`](file:///home/berrybytes/Office/AX_Substrtate/ax-runtime-observer/observer.py) | Autonomous bridge monitoring agent log stream. Detects generic `WAITING_*` states, issues deduplicated `ax suspend task`, monitors worker release, and syncs AX on native wake. |

---

## 4. End-to-End Autonomous Lifecycle Flow (The 8 Stages)

```
[OpenClaw Actor]             [atunnel / Egress]        [External Services]       [Observer / AX]          [Substrate / WorkerPool]
       │                             │                         │                        │                            │
STAGE 1: Local CLI Execution         │                         │                        │                            │
       ├─ nmap -sT -Pn ─────────────┼─────────────────────────┼────────────────────────┼────────────────────────────┤
       │  (Output: /workspace/nmap)  │                         │                        │                            │
STAGE 2: External MCP Query          │                         │                        │                            │
       ├─ POST /mcp (JSON-RPC) ─────►│                         │                        │                            │
       │                             ├─ Egress (SPIFFE) ──────►│ (MCP Server :8000)     │                            │
       │◄────────────────────────────┴◄────────────────────────┤                        │                            │
STAGE 3: External Async API Submit   │                         │                        │                            │
       ├─ POST /api/v1/scans ───────►│                         │                        │                            │
       │                             ├─ Egress (SPIFFE) ──────►│ (Scan API :8080)       │                            │
       │◄────────────────────────────┴◄────────────────────────┤ (Returns 202 Accepted) │                            │
       │  status: WAITING_FOR_EXT    │                         │                        │                            │
       │  loops: time.sleep(5)       │                         │                        │                            │
STAGE 4: Wait Detection & Suspend    │                         │                        │                            │
       │                             │                         │                        ├─ Detects 'WAITING_...'     │
       │                             │                         │                        ├─ ax suspend task ─────────►│
STAGE 5: FULL Snapshot & Worker Free │                         │                        │                            │
       │                             │                         │                        │                            ├─ runsc checkpoint (RAM)
       │                             │                         │                        │                            ├─ S3: pages.img (286ms)
       │ (SANDBOX REMOVED)           │                         │                        │                            ├─ Worker released (0/1)
       │                             │                         │                        │                            │
STAGE 6: External Webhook Callback   │                         │                        │                            │
       │                             │                         ├─ 45s scan completes    │                            │
       │                             │                         ├─ POST :30080/webhook ──┼───────────────────────────►│ (atenet-router)
       │                             │                         │  ate-target-actor      │                            │
STAGE 7: Native Dataplane Resumption │                         │                        │                            │
       │                             │                         │                        │                            ├─ Envoy ext_proc intercepts
       │                             │                         │                        │                            ├─ ResumeActor (gRPC)
       │                             │                         │                        │                            ├─ Restores on free worker
STAGE 8: Process Memory Continuation │                         │                        │                            │
       ├─ Wakes from time.sleep(5) ──┼─────────────────────────┼────────────────────────┼────────────────────────────┤
       ├─ GET /api/v1/scans/job ────►│──────── Egress ────────►│ (Returns findings)     │                            │
       ├─ Writes /workspace/report.md│                         │                        │                            │
       ▼  Task COMPLETED!            │                         │                        │                            │
```

### Detailed Breakdown of the 8 Stages:

#### Stage 1: Local CLI Tool Execution
Inside the gVisor sandbox, [`run_workload.sh`](file:///home/berrybytes/Office/AX_Substrtate/openclaw/agent/run_workload.sh) executes:
```bash
nmap -sT -Pn --unprivileged -p 22,80,8080,3306 security-target.default.svc.cluster.local -oN /workspace/discovery_nmap.txt
```
* **gVisor Compatibility:** The `--unprivileged` and `-sT` (TCP connect scan) flags ensure execution completes without requiring Linux raw socket capabilities (`CAP_NET_RAW`), which are restricted in hardened gVisor microVMs.
* Output is written to durable `/workspace/discovery_nmap.txt`.

#### Stage 2: External MCP Threat Intelligence via Substrate Egress
The runtime script issues a standard MCP JSON-RPC 2.0 query to the external threat intelligence service:
```bash
curl -s -X POST "http://external-mcp-server:8000/mcp" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"lookup_threat_intel","arguments":{"target":"security-target.default.svc.cluster.local"}},"id":1}' \
  -o /workspace/mcp_intel.json
```
* **Egress Verification:** Outbound traffic traverses the worker's local `atunnel` sidecar into `atenet-egress` (Envoy proxy). Envoy validates the Actor's cryptographic certificate and logs:
  `{"actor":"spiffe://substrate-actor.local/atespace/default/actor/openclaw-task","authority":"external-mcp-server:8000","status":200}`
* Intel response containing known CVE exposures (`CVE-2023-38408`, `CVE-2021-41773`) is saved to `/workspace/mcp_intel.json`.

#### Stage 3: Unmodified OpenClaw Launches & Submits External Scan
[`run_workload.sh`](file:///home/berrybytes/Office/AX_Substrtate/openclaw/agent/run_workload.sh) executes `exec python3 /agent/openclaw_agent.py`:
* OpenClaw performs an HTTP POST to `http://external-scan-api:8080/api/v1/scans`.
* `external-scan-api` returns `202 Accepted` with `job_id="ext-scan-001"` and starts an asynchronous 45-second background scan.
* OpenClaw writes its internal state to `/workspace/state.json`:
  ```json
  {"job_id": "ext-scan-001", "status": "WAITING_FOR_EXTERNAL_API", "poll_count": 0}
  ```
* OpenClaw logs `[Iteration #1] Querying status...` and enters standard Python sleep: `time.sleep(5)`.

#### Stage 4: Generic Wait Detection & AX Task Suspension
The [`ax-runtime-observer`](file:///home/berrybytes/Office/AX_Substrtate/ax-runtime-observer/observer.py) pod runs in `ax-system` and streams logs via `kubectl-ate logs actor openclaw-task`:
* **Detection:** Observer parses regex `status='WAITING_FOR_EXTERNAL_API'`.
* **Deduplication Lock:** State transitions from `MONITORING` to `SUSPEND_REQUESTED`, ensuring exactly one suspend call is issued.
* **Action:** Observer executes `ax suspend task openclaw-task -a default`.
* AX Server sets `task.spec.suspend = true`, and `ax-controller` forwards the suspend request to Substrate's `ate-api-server`.

#### Stage 5: FULL gVisor Snapshot & Physical Worker Reclamation
Substrate receives the pause request for Actor `openclaw-task`:
* **Full Memory Dump:** Because of our patch in [`client.go:266`](file:///home/berrybytes/Office/AX_Substrtate/ax/internal/substrate/client.go#L266) (`SNAPSHOT_CONTENT_SCOPE_FULL`), `ateom-gvisor` triggers `runsc checkpoint`.
* **Artifacts Uploaded to RustFS S3 (`ate-snapshots` bucket in 286ms):**
  - `pages.img`: Process virtual memory pages (Python runtime, heap, call stack, variables).
  - `checkpoint.img`: gVisor kernel state, open file descriptors, signal masks.
  - `durable-dir.tar.zstd`: Contents of `/workspace`.
* **Worker Pod Released:** The physical worker container (`ax-system/worker-pool-6dbd6964f6-8b9vz`) tears down the sandbox and transitions to `0/1 ACTORS` (100% FREE).

#### Stage 6: External Scan Completion & Ingress Callback
The background thread in `external-scan-api` sleeps for 45 seconds while the scan completes:
* Upon completion, it dispatches an HTTP POST request to the cluster's ingress gateway:
  `http://kind-control-plane:30080/webhook`
* **Crucial Header Contract:** The request includes the native Substrate routing header:
  ```http
  POST /webhook HTTP/1.1
  Host: kind-control-plane:30080
  Content-Type: application/json
  ate-target-actor: default/openclaw-task
  ```

#### Stage 7: Native `atenet-router` Resumption & Worker Placement
`service/atenet-router` (NodePort 30080 mapping to Envoy port 8080) receives the incoming request:
* **Header Interception:** Envoy's `ext_proc` filter ([`resumer.go`](file:///home/berrybytes/Office/AX_Substrtate/substrate/cmd/atenet/internal/router/ingress/handler.go)) reads `ate-target-actor: default/openclaw-task`.
* **Dynamic Resume:** Because `openclaw-task` is in `ACTOR_STATE_SUSPENDED`, `ext_proc` automatically calls `ResumeActor` via gRPC to `ate-api-server`.
* **Worker Scheduling:** Substrate inspects available capacity in `worker-pool` and assigns an idle worker (`ax-system/worker-pool-6dbd6964f6-2s6cd`).
* **Snapshot Restoration:** Worker downloads `pages.img`, `checkpoint.img`, and durable storage from RustFS S3, and restores the gVisor sandbox via `runsc restore`.
* **AX Control Plane Sync:** The observer detects the actor transition back to `ACTOR_STATE_RUNNING` and executes `ax resume task openclaw-task` to synchronize the task phase.

#### Stage 8: Seamless Process Continuation & Final Report Generation
* **In-RAM Process Continuation:** The Python process inside the restored sandbox wakes up from `time.sleep(5)` at **Iteration #2**. It did **not** restart, did **not** re-execute `main()`, and did **not** re-submit a duplicate scan job!
* OpenClaw queries `GET /api/v1/scans/ext-scan-001`, receives status `COMPLETED` and finding results.
* It parses the findings, correlates with local tools, and writes `/workspace/report.md`.
* State transitions to `COMPLETED` and the process enters quiescent sleep.

---

## 5. Network Configuration & Header Contracts

### 5.1 Ingress Dataplane (`atenet-router`)
Substrate's ingress architecture is defined in `substrate/cmd/atenet/internal/router`:
* **NodePort Service:**
  ```yaml
  apiVersion: v1
  kind: Service
  metadata:
    name: atenet-router
    namespace: ate-system
  spec:
    type: NodePort
    ports:
    - name: http
      port: 80
      targetPort: 8080
      nodePort: 30080
    selector:
      app: atenet-router
  ```
* **Routing Header Contract (`headers.go:29`):**
  - Header name: `ate-target-actor`
  - Value format: `<atespace>/<actor-name>` (e.g., `default/openclaw-task`)
  - The router uses this header to route traffic independently of the HTTP `Host` header. If the actor is suspended, the router parks the request, resumes the actor, and re-routes to the restored worker.

### 5.2 Egress Dataplane (`atenet-egress`)
Outbound traffic from sandboxes is intercepted by local iptables and redirected through the worker's `atunnel` proxy to `atenet-egress`:
* **SPIFFE Authentication:** Every actor is assigned a SPIFFE mTLS client certificate signed by the in-cluster Actor CA:
  `spiffe://substrate-actor.local/atespace/<atespace>/actor/<actor-name>`
* **Envoy Forward Proxy:** `atenet-egress` validates the certificate extension against the Substrate API and logs the verified identity alongside the upstream destination.

### 5.3 Docker Network Addressing
Containers running outside Kubernetes on the `kind` bridge network (`172.18.0.0/16`) are assigned stable hostnames:
* `http://external-mcp-server:8000`: MCP JSON-RPC endpoint.
* `http://external-scan-api:8080`: Async scan API endpoint.
* `http://kind-control-plane:30080`: KinD node gateway reaching `atenet-router`.

CoreDNS inside the cluster forwards non-cluster queries to the KinD node's Docker resolver (`127.0.0.11`), allowing seamless DNS resolution from gVisor sandboxes to external Docker containers.

---

## 6. Scaling to Multi-Actor Fleets ($M > N$)

To demonstrate resource multiplexing where logical tasks exceed physical capacity ($M=5$ actors on $N=3$ workers):

```
Time ──►
[T0: Submit 5 Tasks]
   Worker 1: [Actor 1: Active]
   Worker 2: [Actor 2: Active]
   Worker 3: [Actor 3: Active]
   Queue:    [Actor 4: Pending, Actor 5: Pending]  (Capacity 3/3 SATURATED)

[T1: External Wait & Suspend]
   Actor 1 hits WAITING_FOR_EXTERNAL_API ──► Suspends to S3 ──► Worker 1 FREED!
   Actor 2 hits WAITING_FOR_EXTERNAL_API ──► Suspends to S3 ──► Worker 2 FREED!
   Substrate Scheduler:
   Worker 1: [Actor 4: Active (Scheduled!)]
   Worker 2: [Actor 5: Active (Scheduled!)]

[T2: Ingress Callback Wake]
   External Scan 1 completes ──► POST :30080/webhook (ate-target-actor: default/openclaw-task-1)
   Worker 3 completes Actor 3 ──► Worker 3 FREED!
   Actor 1 restored from S3 onto Worker 3!
```

The fleet manifest is located at [`openclaw/deploy/openclaw-fleet-5.yaml`](file:///home/berrybytes/Office/AX_Substrtate/openclaw/deploy/openclaw-fleet-5.yaml).

---

## 7. Operational Runbook & Verification Commands

### 7.1 Automated 1-Click Cluster Provisioning
To reproduce the entire environment from scratch:
```bash
./setup-all.sh
```
This script executes:
1. KinD cluster creation with containerd mirroring.
2. Substrate system installation (`ate-system`).
3. Compilation & publishing of all container images to local registry `localhost:5001`.
4. AX control plane deployment (`ax-system`).
5. `atenet-router` NodePort 30080 configuration.
6. Launch of external Docker containers on `--net=kind`.
7. Deployment of `ax-runtime-observer` and `openclaw-task`.

### 7.2 Interactive Shell Aliases
Inside the host terminal:
```bash
alias ax='kubectl exec -it -n ax-system deploy/ax-toolbox -- ax'
alias kubectl-ate='kubectl exec -it -n ax-system deploy/ax-toolbox -- kubectl-ate'
```

### 7.3 Real-Time Verification Commands

#### Check Physical Worker Pool Allocation:
```bash
kubectl-ate get workers
# Verify: 3 physical worker pods in ax-system with their IP addresses
```

#### Check Logical Actor States:
```bash
kubectl-ate get actors -a default
# States: ACTOR_STATE_RUNNING -> ACTOR_STATE_SUSPENDED (Worker: <none>) -> ACTOR_STATE_RUNNING
```

#### Inspect Live Observer Transition Logs:
```bash
kubectl logs -n ax-system deployment/ax-runtime-observer -f
```

#### Verify Egress SPIFFE Identity & Access Logs:
```bash
kubectl logs -n ate-system -l app=atenet-egress -c envoy --tail=50
# Look for: "actor":"spiffe://substrate-actor.local/atespace/default/actor/openclaw-task"
```

#### Inspect External Scanner Audit Trail:
```bash
curl -s http://172.18.0.5:8080/api/v1/audit | jq .
```

#### Inspect Final Security Audit Report:
```bash
ax ssh openclaw-task -- cat /workspace/report.md
```

### 7.4 External Services Management
Use [`external-services/manage-external-services.sh`](file:///home/berrybytes/Office/AX_Substrtate/external-services/manage-external-services.sh):
```bash
./external-services/manage-external-services.sh status
./external-services/manage-external-services.sh logs scan
./external-services/manage-external-services.sh restart
```
