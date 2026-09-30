#!/usr/bin/env bash
# ==============================================================================
# Helper Script to Manage External Services Running on Docker 'kind' Network
# ==============================================================================
set -euo pipefail

ACTION="${1:-status}"

case "$ACTION" in
  start)
    echo "==> Starting External Services on Docker 'kind' network..."
    docker build -t external-mcp-server:latest -f external-services/mcp-server/Dockerfile external-services/mcp-server
    docker rm -f external-mcp-server 2>/dev/null || true
    docker run -d --name external-mcp-server --net=kind external-mcp-server:latest

    docker build -t external-scan-api:latest -f external-services/scan-api/Dockerfile external-services/scan-api
    docker rm -f external-scan-api 2>/dev/null || true
    docker run -d --name external-scan-api --net=kind external-scan-api:latest
    echo "==> External Services started successfully!"
    ;;
  stop)
    echo "==> Stopping External Services..."
    docker rm -f external-mcp-server external-scan-api 2>/dev/null || true
    echo "==> Stopped."
    ;;
  restart)
    $0 stop
    $0 start
    ;;
  status)
    echo "==> Docker containers on 'kind' network:"
    docker ps --filter "name=external-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}\t{{.Networks}}"
    echo ""
    echo "==> Testing MCP healthz:"
    curl -s http://172.18.0.4:8000/healthz 2>/dev/null || echo "(172.18.0.4:8000 unreachable directly from host, try via container or kind node)"
    echo ""
    echo "==> Testing Scan API healthz:"
    curl -s http://172.18.0.5:8080/healthz 2>/dev/null || echo "(172.18.0.5:8080 unreachable directly from host, try via container or kind node)"
    ;;
  logs)
    TARGET="${2:-all}"
    if [ "$TARGET" == "mcp" ]; then
      docker logs -f external-mcp-server
    elif [ "$TARGET" == "scan" ]; then
      docker logs -f external-scan-api
    else
      echo "=== External MCP Logs ==="
      docker logs --tail=20 external-mcp-server
      echo "=== External Scan API Logs ==="
      docker logs --tail=20 external-scan-api
    fi
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|logs [mcp|scan]}"
    exit 1
    ;;
esac
