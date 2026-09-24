#!/usr/bin/env bash
set -euo pipefail

export GOTOOLCHAIN=auto
export KO_DOCKER_REPO=localhost:5001
export KO_DEFAULTPLATFORMS="linux/amd64"
export PATH="/go/bin:$PATH"

git config --global --add safe.directory '*'

echo "==> Ensuring ko is installed in builder..."
go install github.com/google/ko@latest

echo "==> Building ateom-gvisor..."
cd /workspace/substrate
ko build --base-import-paths ./cmd/ateom-gvisor

echo "==> Building ax-server..."
cd /workspace/ax
ko build --base-import-paths ./cmd/ax-server

echo "==> Building ax-controller..."
ko build --base-import-paths ./cmd/ax-controller

echo "==> Cross-compiling ax-task-runner for linux/amd64..."
mkdir -p bin/linux_amd64
GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o bin/linux_amd64/ax-task-runner ./cmd/ax-task-runner

echo "==> Building ax-task-runner container image..."
docker build -t localhost:5001/ax-task-runner:latest -f Dockerfile.task-runner .
docker push localhost:5001/ax-task-runner:latest

echo "==> Building ax and kubectl-ate CLI binaries..."
mkdir -p /workspace/bin
go build -trimpath -ldflags="-s -w" -o /workspace/bin/ax ./cmd/ax
cd /workspace/substrate
go build -trimpath -ldflags="-s -w" -o /workspace/bin/kubectl-ate ./cmd/kubectl-ate

echo "==> Building ax-toolbox image..."
cp /usr/local/bin/kubectl /workspace/bin/kubectl

cat << 'EOF' > /workspace/Dockerfile.toolbox
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    procps \
    bash \
    jq \
    vim \
    && rm -rf /var/lib/apt/lists/*

COPY bin/ax /usr/local/bin/ax
COPY bin/kubectl-ate /usr/local/bin/kubectl-ate
COPY bin/kubectl /usr/local/bin/kubectl

WORKDIR /workspace
ENTRYPOINT ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]
EOF

cd /workspace
docker build -t localhost:5001/ax-toolbox:latest -f Dockerfile.toolbox .
docker push localhost:5001/ax-toolbox:latest

echo "==> Cleaning local /workspace/bin so no binaries stay on host..."
rm -rf /workspace/bin /workspace/Dockerfile.toolbox /workspace/ax/bin

echo "==> ALL IMAGES BUILT AND PUBLISHED TO LOCAL REGISTRY!"
