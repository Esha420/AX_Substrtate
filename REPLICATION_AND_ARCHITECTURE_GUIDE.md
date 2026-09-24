# Google AX & Agent Substrate (ATE): Complete Architecture & Replication Guide

This guide provides a comprehensive architectural breakdown and step-by-step instructions to **replicate this exact deployment on another PC** using a single turnkey setup script (`setup-all.sh`).

---

## 1. System Architecture & Topology

```
                                      OTHER PC HOST
                      ┌──────────────────────────────────────────────┐
                      │  Only Docker, KinD, and kubectl required     │
                      │  (Zero Go, Ko, or agent binaries on host OS) │
                      └──────────────────────┬───────────────────────┘
                                             │
                                   kubectl exec / port-forward
                                             │
                                             ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────┐
│  KinD Cluster: "kind" (Node: kindest/node:v1.37.0 with /dev/kvm & containerd registry mirroring)  │
│                                                                                                   │
│  ┌──────────────────────────────────┐  ┌───────────────────────────────────────────────────────┐  │
│  │ Namespace: ax-system             │  │ Namespace: ate-system (Agent Substrate Control Plane) │  │
│  │                                  │  │                                                       │  │
│  │ • ax-server (:8080 HTTP API)     │  │ • ate-api-server (gRPC :443 Control API)              │  │
│  │ • ax-controller (Task Reconciler)│  │ • ate-controller (WorkerPool & NetPolicy Reconciler)  │  │
│  │ • ax-redis (Task state & stream) │  │ • atelet (DaemonSet interfacing gVisor runsc)        │  │
│  │ • ax-toolbox (In-cluster CLI)    │  │ • atenet-router & atenet-egress (Envoy Proxies)       │  │
│  │ • worker-pool (3 Worker Pods)    │  │ • postgres-0 (Substrate metadata database)           │  │
│  │   └── ateom-gvisor runtime       │  │ • rustfs (S3-compatible bucket: s3://ate-snapshots/) │  │
│  └──────────────────┬───────────────┘  │ • podcertificate-controller (mTLS SPIFFE CA)          │  │
│                     │                  └───────────────────────────────────────────────────────┘  │
│                     │                                                                             │
│                     ▼ Schedules onto WorkerPool                                                   │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ gVisor Hardware-Virtualized Actor: security-analyzer (Atespace: default)                    │  │
│  │                                                                                             │  │
│  │  • ax-task-runner (PID 1 supervisor, :80 readyz + h2c guest tunnel)                         │  │
│  │  • Python Security Agent (/agent/main.py)                                                   │  │
│  │  • Nmap scanner engine (/workspace/scans)                                                   │  │
│  │  • Durable Volume (/workspace mounted and snapshotted to rustfs S3)                        │  │
│  └──────────────────┬──────────────────────────────────────────────────────────────────────────┘  │
│                     │                                                                             │
│                     │ Probes internal network                                                     │
│                     ▼                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ Isolated Target Service: security-target (Namespace: default)                               │  │
│  │ • Port 22 (OpenSSH 8.9p1)         • Port 80 (Nginx 1.24 / PHP 8.1)                          │  │
│  │ • Port 8080 (Apache Admin Portal) • Port 3306 (MySQL 5.7 Database)                          │  │
│  └─────────────────────────────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
                                             ▲
                                             │ Pulls images via containerd certs.d mirror
                                ┌────────────┴───────────┐
                                │ Local Registry         │
                                │ Container:             │
                                │   kind-registry:5000   │
                                │ Host: localhost:5001   │
                                └────────────────────────┘
```

---

## 2. Prerequisites for the Other PC

Before running the setup on the other PC, ensure the following are present:

1. **Operating System**: Linux x86_64 (Ubuntu 20.04/22.04/24.04, Debian 11/12, Fedora, Arch, etc.).
2. **Hardware Virtualization**:
   - Check that KVM hardware virtualization is enabled in BIOS/UEFI:
     ```bash
     ls -l /dev/kvm
     ```
   - If `/dev/kvm` exists, gVisor will run at near-native hardware speed.
3. **Software Installed on Host**:
   - **Docker** (`docker info` works without sudo; user is in `docker` group).
   - **KinD** (`kind --version` installed).
   - **kubectl** (`kubectl version --client` installed).
4. **Tools NOT Required on Host**:
   - You **do NOT need** `go`, `ko`, `nmap`, or `python` installed on the host. All compilation and image building occurs inside hermetic builder containers.

---

## 3. Replication Steps (Single-Script Deployment)

### Step 1: Copy the Directory to the Other PC
Transfer your folder `/home/esha/Office/ax_substrate` to any location on the target PC (e.g. `~/ax_substrate` or `/home/<user>/ax_substrate`).

### Step 2: Open Terminal and Run the Setup Script
```bash
cd /path/to/ax_substrate
chmod +x setup-all.sh
./setup-all.sh
```

---

## 4. What `setup-all.sh` Does Under the Hood

The script runs end-to-end automatically without requiring interactive prompts:

1. **Pre-flight Validation**: Verifies that Docker, KinD, kubectl, and `/dev/kvm` are ready.
2. **KinD Cluster Provisioning**:
   - Starts local registry `kind-registry` on port `5001`.
   - Creates KinD cluster using `kindest/node:v1.37.0` with feature gates: `ClusterTrustBundle`, `ClusterTrustBundleProjection`, and `PodCertificateRequest`.
   - Configures containerd v2 on the KinD node with `config_path = "/etc/containerd/certs.d"` so `localhost:5001` queries `http://kind-registry:5000`.
3. **Substrate Deployment (`ate-system`)**:
   - Runs `substrate/hack/install-ate-kind.sh --deploy-ate-system` inside an ephemeral `golang:1.24` container.
   - Deploys Postgres, RustFS S3, mTLS certificate authority, and envoy proxies.
4. **Builds All Images Hermetically**:
   - Compiles Go binaries and builds container images:
     - `localhost:5001/ateom-gvisor:latest`
     - `localhost:5001/ax-server:latest`
     - `localhost:5001/ax-controller:latest`
     - `localhost:5001/ax-task-runner:latest`
     - `localhost:5001/ax-toolbox:latest`
     - `localhost:5001/security-agent:latest`
   - Automatically cleans up all compiled binaries from the host disk.
5. **Deploys AX Control Plane & WorkerPool**:
   - Deploys Redis to `ax-system`.
   - Deploys `WorkerPool` scaled to **3 replicas** for concurrent sandbox execution.
   - Deploys `ax-server`, `ax-controller`, and `ax-toolbox`.
6. **Initializes Atespaces & Targets**:
   - Creates `default` and `ax-system` atespaces in Substrate.
   - Deploys `security-target` (ports 22, 80, 8080, 3306).
   - Resolves the exact cryptographic image digest (`@sha256:...`) of `security-agent` and submits the AX Task `security-analyzer`.

---

## 5. Technical Modifications & Bug Fixes Applied to Upstream

### In Repository `substrate/`:
1. **`hack/create-kind-cluster.sh`**:
   - Fixed `certificates.k8s.io/v1beta1` missing in default KinD node images by upgrading to `kindest/node:v1.37.0`.
   - Patched containerd v2 configuration by adding `config_path = "/etc/containerd/certs.d"` to `/etc/containerd/config.toml` inside the node.
2. **`hack/kind.sh`**:
   - Replaced host kind lookup with `go run -C "${ROOT}/hack/tools/kind" sigs.k8s.io/kind "$@"` for version pinning.

### In Repository `ax/`:
1. **`.dockerignore`**:
   - Upstream contained `bin/`, which caused `COPY bin/linux_amd64/ax-task-runner` in `Dockerfile.task-runner` to fail. Whitelisted with `!bin/linux_amd64/ax-task-runner`.
2. **`deploy/ax-controller.yaml`**:
   - Directed snapshot store to in-cluster S3 via `AX_SNAPSHOTS_BUCKET="s3://ate-snapshots/"`.
   - Updated container image from `ko://...` to `localhost:5001/ax-controller:latest`.
3. **`deploy/ax-server.yaml`**:
   - Updated container image from `ko://...` to `localhost:5001/ax-server:latest`.

---

## 6. How to Interact with the Deployed System

### Set Up Terminal Aliases (No Host Installation Needed)
```bash
alias ax='kubectl exec -it -n ax-system deploy/ax-toolbox -- ax'
alias kubectl-ate='kubectl exec -it -n ax-system deploy/ax-toolbox -- kubectl-ate'
```

### Verification & Operations Cheat Sheet

```bash
# 1. View task lifecycle and conditions
ax get tasks
ax describe task security-analyzer

# 2. View live scan output & generated AI report
ax ssh security-analyzer -- tail -f /workspace/security.log
ax ssh security-analyzer -- cat /workspace/report.md
ax ssh security-analyzer -- ls -la /workspace/scans

# 3. View Substrate workers and gVisor sandboxed actors
kubectl-ate get workers
kubectl-ate get actors -a default

# 4. Demonstrate Suspend & Resume (State Snapshot Persistence)
ax suspend task security-analyzer
ax get tasks                        # Phase shows Suspended, worker freed
ax resume task security-analyzer
ax ssh security-analyzer -- cat /workspace/report.md  # Report remains intact!

# 5. Re-run scan on demand
ax ssh security-analyzer -- python3 /agent/main.py
```

---

## 7. Visualizing the Deployment

### A. Visual Security Dashboard (HTML)
Open the generated interactive dashboard in any browser:
```bash
xdg-open security_dashboard.html
```
Displays the attack surface radar, port states, service version cards, and Nemotron AI vulnerability ratings.

### B. Distributed Tracing in Jaeger
Visualize real-time gRPC spans between AX, Substrate, and gVisor:
```bash
kubectl port-forward -n otel-system svc/jaeger 16686:16686
```
Open **`http://localhost:16686`** in your browser. Select service `ate-api` or `ax-controller` and click **Find Traces**.

---

## 8. Complete Teardown Procedure

To completely remove the cluster, local registry, image layers, and build caches with **zero leftovers on the host**:

```bash
kind delete cluster --name kind
docker rm -f kind-registry
docker volume rm go-build-cache go-mod-cache
```
