#!/usr/bin/env bash
# ==============================================================================
# Google AX on Agent Substrate (ATE) - 1-Click Cluster Replication & Setup
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "===================================================================="
echo " Starting Full Automated Setup for Google AX + Agent Substrate"
echo "===================================================================="

# 1. Pre-flight Checks
echo "==> Checking prerequisites..."
command -v docker >/dev/null 2>&1 || { echo >&2 "[ERROR] 'docker' is required but not installed. Aborting."; exit 1; }
docker info >/dev/null 2>&1 || { echo >&2 "[ERROR] Docker daemon is not running or current user lacks docker permissions. Aborting."; exit 1; }
command -v kind >/dev/null 2>&1 || { echo >&2 "[ERROR] 'kind' is required but not installed. Aborting."; exit 1; }
command -v kubectl >/dev/null 2>&1 || { echo >&2 "[ERROR] 'kubectl' is required but not installed. Aborting."; exit 1; }

if [ ! -e /dev/kvm ]; then
    echo >&2 "[WARN] /dev/kvm device not detected. Hardware acceleration for gVisor may be degraded or disabled."
fi

# Ensure ~/.kube exists
mkdir -p "${HOME}/.kube"

DOCKER_BIN="$(command -v docker)"
KUBECTL_BIN="$(command -v kubectl)"

# 2. KinD Cluster & Substrate Setup
echo "==> Provisioning KinD cluster with containerd mirroring and Substrate..."
docker run --rm --net=host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${DOCKER_BIN}":/usr/bin/docker:ro \
  -v "${KUBECTL_BIN}":/usr/local/bin/kubectl:ro \
  -v "${HOME}/.kube":/root/.kube \
  -v "${SCRIPT_DIR}":/workspace \
  -v go-build-cache:/root/.cache/go-build \
  -v go-mod-cache:/go/pkg/mod \
  -w /workspace/substrate \
  golang:1.24 bash -c "
    set -euo pipefail
    git config --global --add safe.directory '*'
    if ! kind get clusters | grep -q '^kind$'; then
      echo '==> Creating KinD cluster kindest/node:v1.37.0...'
      ./hack/create-kind-cluster.sh --node-image=kindest/node:v1.37.0
    else
      echo '==> KinD cluster already exists. Continuing...'
    fi
    echo '==> Ensuring ko is installed...'
    export GOTOOLCHAIN=auto
    go install github.com/google/ko@latest
    export PATH=\"/go/bin:\$PATH\"
    echo '==> Deploying Substrate System (ate-system)...'
    ./hack/install-ate-kind.sh --deploy-ate-system
  "

# Merge kubeconfig permissions for host if needed
chmod 600 "${HOME}/.kube/config" 2>/dev/null || true
kubectl config use-context kind-kind >/dev/null 2>&1 || true

echo "==> Waiting for Agent Substrate control plane to be Ready..."
kubectl wait --for=condition=Available deployment/ate-api-server -n ate-system --timeout=180s
kubectl wait --for=condition=Available deployment/rustfs -n ate-system --timeout=180s

# 3. Build & Publish All Images (AX Control Plane, Runner, Toolbox, Security Agent)
echo "==> Building and publishing all container images to local registry (localhost:5001)..."
docker run --rm --net=host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${DOCKER_BIN}":/usr/bin/docker:ro \
  -v "${KUBECTL_BIN}":/usr/local/bin/kubectl:ro \
  -v "${HOME}/.kube":/root/.kube \
  -v "${SCRIPT_DIR}":/workspace \
  -v go-build-cache:/root/.cache/go-build \
  -v go-mod-cache:/go/pkg/mod \
  -w /workspace \
  golang:1.24 bash -c "
    set -euo pipefail
    git config --global --add safe.directory '*'
    export GOTOOLCHAIN=auto
    export KO_DOCKER_REPO=localhost:5001
    export KO_DEFAULTPLATFORMS='linux/amd64'
    export PATH='/go/bin:\$PATH'
    go install github.com/google/ko@latest

    echo '==> Building ateom-gvisor...'
    cd /workspace/substrate && ko build --base-import-paths ./cmd/ateom-gvisor

    echo '==> Building ax-server...'
    cd /workspace/ax && ko build --base-import-paths ./cmd/ax-server

    echo '==> Building ax-controller...'
    ko build --base-import-paths ./cmd/ax-controller

    echo '==> Cross-compiling ax-task-runner for linux/amd64...'
    mkdir -p bin/linux_amd64
    GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o bin/linux_amd64/ax-task-runner ./cmd/ax-task-runner

    echo '==> Building ax-task-runner container image...'
    docker build -t localhost:5001/ax-task-runner:latest -f Dockerfile.task-runner .
    docker push localhost:5001/ax-task-runner:latest

    echo '==> Building ax and kubectl-ate CLI binaries for in-cluster toolbox...'
    mkdir -p /workspace/bin
    go build -trimpath -ldflags='-s -w' -o /workspace/bin/ax ./cmd/ax
    cd /workspace/substrate
    go build -trimpath -ldflags='-s -w' -o /workspace/bin/kubectl-ate ./cmd/kubectl-ate

    echo '==> Building ax-toolbox image...'
    cp /usr/local/bin/kubectl /workspace/bin/kubectl
    cat << 'EOF' > /workspace/Dockerfile.toolbox
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates git procps bash jq vim \
    && rm -rf /var/lib/apt/lists/*
COPY bin/ax /usr/local/bin/ax
COPY bin/kubectl-ate /usr/local/bin/kubectl-ate
COPY bin/kubectl /usr/local/bin/kubectl
WORKDIR /workspace
ENTRYPOINT [\"/bin/bash\", \"-c\", \"trap : TERM INT; sleep infinity & wait\"]
EOF
    cd /workspace
    docker build -t localhost:5001/ax-toolbox:latest -f Dockerfile.toolbox .
    docker push localhost:5001/ax-toolbox:latest

    echo '==> Building security-agent image...'
    docker build -t localhost:5001/security-agent:latest -f security-agent/Dockerfile security-agent
    docker push localhost:5001/security-agent:latest

    echo '==> Cleaning temporary build binaries from host workspace...'
    rm -rf /workspace/bin /workspace/Dockerfile.toolbox /workspace/ax/bin
  "

# 4. Deploy AX Control Plane, Redis, WorkerPool, and Toolbox
echo "==> Deploying AX Redis state store..."
kubectl apply -f ax/deploy/redis.yaml

echo "==> Deploying Substrate WorkerPool (3 replicas, gVisor)..."
cat << 'EOF' | kubectl apply -f -
apiVersion: ate.dev/v1alpha1
kind: WorkerPool
metadata:
  name: worker-pool
  namespace: ax-system
spec:
  replicas: 3
  workerImage: localhost:5001/ateom-gvisor:latest
  sandboxClass: gvisor
EOF

echo "==> Deploying AX Server and Controller..."
kubectl apply -f ax/deploy/ax-server.yaml
kubectl apply -f ax/deploy/ax-controller.yaml

echo "==> Deploying In-Cluster Admin Toolbox..."
kubectl apply -f ax/deploy/ax-toolbox.yaml

echo "==> Waiting for AX pods to become Ready..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=ax-server -n ax-system --timeout=120s
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=ax-controller -n ax-system --timeout=120s
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=ax-toolbox -n ax-system --timeout=120s
kubectl wait --for=condition=Available deployment/worker-pool -n ax-system --timeout=120s

# 5. Initialize Substrate Atespaces and Templates
echo "==> Initializing Substrate atespaces..."
kubectl exec -n ax-system deploy/ax-toolbox -- kubectl-ate create atespace default 2>/dev/null || true
kubectl exec -n ax-system deploy/ax-toolbox -- kubectl-ate create atespace ax-system 2>/dev/null || true

# 6. Deploy Mock Target
echo "==> Deploying isolated Mock Security Target (default namespace)..."
kubectl apply -f ax/deploy/security-target.yaml
kubectl wait --for=condition=Ready pod -l app=security-target -n default --timeout=120s

# 7. Pin Exact Security Agent Image Digest and Deploy Task
echo "==> Resolving security-agent digest and deploying AX Task..."
SECURITY_AGENT_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' localhost:5001/security-agent:latest)
echo "Resolved security-agent digest: ${SECURITY_AGENT_DIGEST}"

cat << EOF > ax/deploy/security-task.yaml
apiVersion: ax.io/v1alpha1
kind: Task
metadata:
  name: security-analyzer
  atespace: default
spec:
  image: "${SECURITY_AGENT_DIGEST}"
  command:
    - python3
    - /agent/main.py
  env:
    - name: TARGET
      value: "security-target.default.svc.cluster.local"
    - name: WORKSPACE
      value: "/workspace"
    - name: NVIDIA_MODEL
      value: "nvidia/nemotron-3-super-120b-a12b"
  debug: true
EOF

kubectl exec -n ax-system deploy/ax-toolbox -- ax delete task security-analyzer 2>/dev/null || true
cat ax/deploy/security-task.yaml | kubectl exec -i -n ax-system deploy/ax-toolbox -- ax apply -f -

echo "===================================================================="
echo " SETUP COMPLETED SUCCESSFULLY!"
echo "===================================================================="
echo "To interact with AX and Substrate without installing host binaries, run:"
echo "  alias ax='kubectl exec -it -n ax-system deploy/ax-toolbox -- ax'"
echo "  alias kubectl-ate='kubectl exec -it -n ax-system deploy/ax-toolbox -- kubectl-ate'"
echo ""
echo "Try these commands:"
echo "  ax get tasks"
echo "  ax describe task security-analyzer"
echo "  ax ssh security-analyzer -- tail -f /workspace/security.log"
echo "  ax ssh security-analyzer -- cat /workspace/report.md"
echo "  kubectl-ate get workers"
echo "  kubectl-ate get actors -a default"
echo "===================================================================="
