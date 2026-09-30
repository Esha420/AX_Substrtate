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
KIND_BIN="$(command -v kind)"

# 2. KinD Cluster & Substrate Setup
echo "==> Provisioning KinD cluster with containerd mirroring and Substrate..."
docker run --rm --net=host \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "${DOCKER_BIN}":/usr/bin/docker:ro \
  -v "${KUBECTL_BIN}":/usr/local/bin/kubectl:ro \
  -v "${KIND_BIN}":/usr/local/bin/kind:ro \
  -v "${HOME}/.kube":/root/.kube \
  -v "${SCRIPT_DIR}":/workspace \
  -v go-build-cache:/root/.cache/go-build \
  -v go-mod-cache:/go/pkg/mod \
  -w /workspace/substrate \
  golang:1.24 bash -c "
    set -euo pipefail
    git config --global --add safe.directory '*'
    export GOTOOLCHAIN=auto
    if ! ./hack/kind.sh get clusters | grep -q '^kind$'; then
      echo '==> Creating KinD cluster kindest/node:v1.37.0...'
      ./hack/create-kind-cluster.sh --node-image=kindest/node:v1.37.0
    else
      echo '==> KinD cluster already exists. Continuing...'
    fi
    echo '==> Ensuring ko is installed...'
    go install github.com/google/ko@latest
    export PATH=\"/go/bin:\$PATH\"
    echo '==> Deploying Substrate System (ate-system)...'
    ./hack/install-ate-kind.sh --deploy-ate-system --rollout-timeout 300s
  "

# Merge kubeconfig permissions for host if needed
chmod 600 "${HOME}/.kube/config" 2>/dev/null || true
kubectl config use-context kind-kind >/dev/null 2>&1 || true

echo "==> Waiting for Agent Substrate control plane to be Ready..."
kubectl wait --for=condition=Available deployment/ate-api-server -n ate-system --timeout=300s
kubectl wait --for=condition=Available deployment/rustfs -n ate-system --timeout=300s

echo "==> Ensuring Dash0 credentials Secret exists in otel-system..."
kubectl create secret generic dash0-credentials \
  --namespace=otel-system \
  --from-literal=DASH0_ENDPOINT="https://ingress.us-west-2.aws.dash0.com" \
  --from-literal=DASH0_AUTH_TOKEN="auth_bQbms6SNZuQWbHz8WmSStANFhNfWVF3B" \
  --from-literal=DASH0_DATASET="default" \
  --dry-run=client -o yaml | kubectl apply -f -


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
    export PATH=\"/go/bin:\$PATH\"
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

    echo '==> Building openclaw-agent image...'
    docker build -t localhost:5001/openclaw-agent:latest -f openclaw/Dockerfile openclaw
    docker push localhost:5001/openclaw-agent:latest

    echo '==> Building ax-runtime-observer image...'
    docker build -t localhost:5001/ax-runtime-observer:latest -f ax-runtime-observer/Dockerfile ax-runtime-observer
    docker push localhost:5001/ax-runtime-observer:latest

    echo '==> Building external services images (MCP server & Scan API)...'
    docker build -t external-mcp-server:latest -f external-services/mcp-server/Dockerfile external-services/mcp-server
    docker build -t external-scan-api:latest -f external-services/scan-api/Dockerfile external-services/scan-api

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
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=ax-server -n ax-system --timeout=300s
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=ax-controller -n ax-system --timeout=300s
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=ax-toolbox -n ax-system --timeout=300s
kubectl wait --for=condition=Available deployment/worker-pool -n ax-system --timeout=300s

# 5. Initialize Substrate Atespaces and Templates
echo "==> Initializing Substrate atespaces..."
kubectl exec -n ax-system deploy/ax-toolbox -- kubectl-ate create atespace default 2>/dev/null || true
kubectl exec -n ax-system deploy/ax-toolbox -- kubectl-ate create atespace ax-system 2>/dev/null || true

# 6. Deploy Mock Target
echo "==> Deploying isolated Mock Security Target (default namespace)..."
kubectl apply -f ax/deploy/security-target.yaml
kubectl wait --for=condition=Ready pod -l app=security-target -n default --timeout=120s

# 7. Configure Ingress NodePort for atenet-router (Native Resumption Dataplane)
echo "==> Configuring atenet-router Ingress NodePort (30080)..."
kubectl patch svc atenet-router -n ate-system -p '{"spec": {"type": "NodePort", "ports": [{"name": "http", "port": 80, "nodePort": 30080, "targetPort": 8080}]}}'

# 8. Launch External Services Outside Kubernetes (Docker kind bridge network)
echo "==> Launching External Services outside Kubernetes on kind bridge network..."
docker rm -f external-mcp-server 2>/dev/null || true
docker run -d --name external-mcp-server --net=kind external-mcp-server:latest
docker rm -f external-scan-api 2>/dev/null || true
docker run -d --name external-scan-api --net=kind external-scan-api:latest

# 9. Deploy AX Runtime Lifecycle Observer
echo "==> Deploying AX Runtime Lifecycle Observer..."
kubectl apply -f ax-runtime-observer/observer-deployment.yaml
kubectl rollout status deployment/ax-runtime-observer -n ax-system --timeout=120s

# 10. Pin Exact OpenClaw Image Digest and Deploy Task
echo "==> Resolving openclaw-agent digest and deploying OpenClaw Task..."
OPENCLAW_DIGEST=$(docker inspect --format='{{index .RepoDigests 0}}' localhost:5001/openclaw-agent:latest)
echo "Resolved openclaw-agent digest: ${OPENCLAW_DIGEST}"

sed -i "s|image: .*|image: \"${OPENCLAW_DIGEST}\"|" openclaw/deploy/openclaw-task.yaml
kubectl exec -n ax-system deploy/ax-toolbox -- ax delete task openclaw-task 2>/dev/null || true
cat openclaw/deploy/openclaw-task.yaml | kubectl exec -i -n ax-system deploy/ax-toolbox -- ax apply -f -

echo "===================================================================="
echo " SETUP COMPLETED SUCCESSFULLY!"
echo "===================================================================="
echo "To interact with AX and Substrate without installing host binaries, run:"
echo "  alias ax='kubectl exec -it -n ax-system deploy/ax-toolbox -- ax'"
echo "  alias kubectl-ate='kubectl exec -it -n ax-system deploy/ax-toolbox -- kubectl-ate'"
echo ""
echo "Try these commands:"
echo "  ax get tasks"
echo "  ax describe task openclaw-task"
echo "  kubectl logs -n ax-system -l app=ax-runtime-observer -f"
echo "  kubectl-ate get workers"
echo "  kubectl-ate get actors -a default"
echo "  ax ssh openclaw-task -- cat /workspace/report.md"
echo "  docker logs -f external-scan-api"
echo "===================================================================="

