#!/bin/bash
set -e

WORKSPACE="${WORKSPACE:-/workspace}"
TARGET="${TARGET:-security-target.default.svc.cluster.local}"
MCP_URL="${MCP_URL:-http://external-mcp-server:8000/mcp}"
TASK_NAME="${TASK_NAME:-$(hostname)}"
ROLE="${ROLE:-}"

if [ -z "$ROLE" ]; then
  if [[ "$TASK_NAME" == *"research"* ]]; then
    ROLE="research"
  elif [[ "$TASK_NAME" == *"data"* ]]; then
    ROLE="data"
  elif [[ "$TASK_NAME" == *"monitoring"* ]] || [[ "$TASK_NAME" == *"probe"* ]]; then
    ROLE="monitoring"
  elif [[ "$TASK_NAME" == *"document"* ]] || [[ "$TASK_NAME" == *"render"* ]]; then
    ROLE="document"
  else
    ROLE="security"
  fi
fi

mkdir -p "$WORKSPACE"

echo "================================================================="
echo "[OpenClaw Runtime Tooling] Initializing Multi-Tool Execution Pipeline"
echo "Actor Task: $TASK_NAME | Role: $ROLE | Workspace: $WORKSPACE"
echo "================================================================="

# Stage 1: Domain-specific CLI initialization
echo "[Stage 1/3] Executing local CLI initialization for role: $ROLE..."
if [ "$ROLE" = "security" ]; then
  nmap -sT -Pn --unprivileged -p 22,80,8080,3306 "$TARGET" -oN "$WORKSPACE/discovery_nmap.txt" 2>/dev/null || echo "Target probed" > "$WORKSPACE/discovery_nmap.txt"
else
  echo "Local environment initialized for $ROLE" > "$WORKSPACE/init_${ROLE}.txt"
fi
echo "[Stage 1/3] Local initialization completed."

# Stage 2: External Fast MCP Call (tailored per role)
echo "[Stage 2/3] Executing external MCP tool call over Substrate egress for role: $ROLE..."
if [ "$ROLE" = "research" ]; then
  curl -s -X POST "$MCP_URL" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"fetch_keywords\",\"arguments\":{\"topic\":\"Autonomous Multi-Agent Multiplexing\"}},\"id\":1}" \
    -o "$WORKSPACE/mcp_intel.json"
elif [ "$ROLE" = "data" ]; then
  curl -s -X POST "$MCP_URL" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_dataset_schema\",\"arguments\":{\"dataset\":\"financial_telemetry_stream\"}},\"id\":1}" \
    -o "$WORKSPACE/mcp_intel.json"
elif [ "$ROLE" = "monitoring" ]; then
  curl -s -X POST "$MCP_URL" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"ping_endpoints\",\"arguments\":{\"target\":\"core-infra-mesh\"}},\"id\":1}" \
    -o "$WORKSPACE/mcp_intel.json"
elif [ "$ROLE" = "document" ]; then
  curl -s -X POST "$MCP_URL" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"validate_template\",\"arguments\":{\"template_name\":\"quarterly-technical-audit\"}},\"id\":1}" \
    -o "$WORKSPACE/mcp_intel.json"
else
  curl -s -X POST "$MCP_URL" \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"lookup_threat_intel\",\"arguments\":{\"target\":\"$TARGET\"}},\"id\":1}" \
    -o "$WORKSPACE/mcp_intel.json"
fi
echo "[Stage 2/3] External Fast MCP query completed. Saved to $WORKSPACE/mcp_intel.json"

# Stage 3: Launch Unmodified OpenClaw Autonomous Workload
echo "[Stage 3/3] Launching unmodified OpenClaw agent workflow..."
export ROLE
exec python3 /agent/openclaw_agent.py
