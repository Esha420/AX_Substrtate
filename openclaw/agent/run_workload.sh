#!/bin/bash
set -e

WORKSPACE="${WORKSPACE:-/workspace}"
TARGET="${TARGET:-security-target.default.svc.cluster.local}"
MCP_URL="${MCP_URL:-http://external-mcp-server:8000/mcp}"

mkdir -p "$WORKSPACE"

echo "================================================================="
echo "[OpenClaw Runtime Tooling] Initializing Multi-Tool Execution Pipeline"
echo "Target Host: $TARGET | Workspace: $WORKSPACE"
echo "================================================================="

# Stage 1: Local CLI Tool Execution (nmap)
echo "[Stage 1/3] Executing local CLI discovery tool: nmap..."
nmap -sT -Pn --unprivileged -p 22,80,8080,3306 "$TARGET" -oN "$WORKSPACE/discovery_nmap.txt"
echo "[Stage 1/3] Local discovery completed. Findings saved to $WORKSPACE/discovery_nmap.txt"

# Stage 2: External MCP Server Tool Execution (via Egress)
echo "[Stage 2/3] Executing external MCP tool call over Substrate egress: lookup_threat_intel..."
curl -s -X POST "$MCP_URL" \
  -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"lookup_threat_intel\",\"arguments\":{\"target\":\"$TARGET\"}},\"id\":1}" \
  -o "$WORKSPACE/mcp_intel.json"
echo "[Stage 2/3] External MCP query completed. Intel saved to $WORKSPACE/mcp_intel.json"

# Stage 3: Launch Unmodified OpenClaw Autonomous Workload
echo "[Stage 3/3] Launching unmodified OpenClaw agent workflow..."
exec python3 /agent/openclaw_agent.py
