#!/usr/bin/env python3
"""
External MCP Security Intelligence Server
Standard MCP JSON-RPC 2.0 endpoint running outside Kubernetes on the kind bridge network.
Completely decoupled from AX, Substrate, and Kubernetes.
"""

import json
import logging
import os
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ExternalMCP] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

PORT = int(os.environ.get("PORT", "8000"))

INTEL_DATABASE = {
    "security-target.default.svc.cluster.local": {
        "threat_score": 8.7,
        "classification": "VULNERABLE_INFRASTRUCTURE",
        "known_exposures": [
            {
                "cve": "CVE-2023-38408",
                "component": "OpenSSH",
                "severity": "CRITICAL",
                "summary": "PKCS#11 provider remote code execution flaw in ssh-agent forwarding"
            },
            {
                "cve": "CVE-2021-41773",
                "component": "Apache httpd",
                "severity": "HIGH",
                "summary": "Path traversal and remote code execution in Apache HTTP Server 2.4.49/2.4.50"
            }
        ],
        "tags": ["edge-service", "ssh-accessible", "external-target"]
    }
}

REMEDIATIONS = {
    "CVE-2023-38408": "Upgrade openssh-client and openssh-server to >= 9.3p2. Restrict PKCS#11 provider libraries.",
    "CVE-2021-41773": "Upgrade Apache httpd to >= 2.4.51. Ensure directory traversal configurations are explicitly denied."
}

def handle_jsonrpc(req_body):
    try:
        rpc = json.loads(req_body)
    except Exception as e:
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {e}"}, "id": None}

    req_id = rpc.get("id")
    method = rpc.get("method")
    params = rpc.get("params", {})

    logging.info(f"Received JSON-RPC request: method='{method}' id='{req_id}'")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "mcp-security-intel",
                    "version": "1.0.0"
                },
                "capabilities": {
                    "tools": {}
                }
            }
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "lookup_threat_intel",
                        "description": "Fetch real-time threat intelligence and vulnerability signatures for target hostname",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string", "description": "Target hostname or IP"}
                            },
                            "required": ["target"]
                        }
                    },
                    {
                        "name": "fetch_keywords",
                        "description": "Fetch literature keywords and citation graphs for research topics",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "topic": {"type": "string", "description": "Research topic"}
                            },
                            "required": ["topic"]
                        }
                    },
                    {
                        "name": "get_dataset_schema",
                        "description": "Retrieve schema metadata and summary statistics for tabular datasets",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "dataset": {"type": "string", "description": "Dataset name"}
                            },
                            "required": ["dataset"]
                        }
                    },
                    {
                        "name": "ping_endpoints",
                        "description": "Perform low-latency ping checks on distributed infrastructure endpoints",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string", "description": "Infrastructure target or endpoint group"}
                            },
                            "required": ["target"]
                        }
                    },
                    {
                        "name": "validate_template",
                        "description": "Validate markdown/PDF document templates against structural schemas",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "template_name": {"type": "string", "description": "Template identifier"}
                            },
                            "required": ["template_name"]
                        }
                    },
                    {
                        "name": "get_cve_remediation",
                        "description": "Retrieve prioritized remediation playbooks for identified CVEs",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "cve_id": {"type": "string", "description": "CVE identifier"}
                            },
                            "required": ["cve_id"]
                        }
                    }
                ]
            }
        }

    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        logging.info(f"Invoking tool: {tool_name} with arguments: {args}")

        if tool_name == "lookup_threat_intel":
            target = args.get("target", "")
            intel = INTEL_DATABASE.get(target, {
                "threat_score": 5.0,
                "classification": "UNCLASSIFIED_HOST",
                "known_exposures": [],
                "tags": ["general-host"]
            })
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(intel, indent=2)
                        }
                    ]
                }
            }

        elif tool_name == "fetch_keywords":
            topic = args.get("topic", "Autonomous Multi-Agent Multiplexing")
            res = {
                "topic": topic,
                "keywords": ["actor-migration", "checkpoint-restore", "gVisor-s3", "cr-dts", "stateful-agents"],
                "citation_count": 27,
                "domain": "Computer Systems & Autonomous Software"
            }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, indent=2)
                        }
                    ]
                }
            }

        elif tool_name == "get_dataset_schema":
            dataset = args.get("dataset", "telemetry-metrics-stream")
            res = {
                "dataset": dataset,
                "record_count": 1420500,
                "columns": [
                    {"name": "timestamp_ns", "type": "int64", "indexed": True},
                    {"name": "actor_id", "type": "string", "indexed": True},
                    {"name": "cpu_utilization", "type": "float32", "indexed": False},
                    {"name": "memory_resident_bytes", "type": "int64", "indexed": False}
                ],
                "schema_version": "v3.1"
            }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, indent=2)
                        }
                    ]
                }
            }

        elif tool_name == "ping_endpoints":
            target = args.get("target", "infra-mesh")
            res = {
                "probed_nodes": ["edge-gateway-01", "edge-gateway-02", "storage-backend"],
                "latency_metrics": {"min_ms": 1.2, "avg_ms": 4.1, "max_ms": 8.7},
                "packet_loss_pct": 0.0,
                "system_status": "OPTIMAL"
            }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, indent=2)
                        }
                    ]
                }
            }

        elif tool_name == "validate_template":
            template_name = args.get("template_name", "quarterly-technical-audit")
            res = {
                "template_name": template_name,
                "syntax_valid": True,
                "required_sections": ["Executive Summary", "Architectural Findings", "Remediation Matrix"],
                "engine": "Markdown-AST-v2"
            }
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(res, indent=2)
                        }
                    ]
                }
            }

        elif tool_name == "get_cve_remediation":
            cve_id = args.get("cve_id", "")
            remediation = REMEDIATIONS.get(cve_id, "Apply standard vendor security updates and harden network access.")
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps({"cve": cve_id, "remediation": remediation}, indent=2)
                        }
                    ]
                }
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method/Tool '{tool_name}' not found"}
        }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method '{method}'"}
    }

class MCPHTTPHandler(BaseHTTPRequestHandler):
    def _send_json(self, status, data):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ["", "/", "/healthz"]:
            self._send_json(200, {
                "status": "HEALTHY",
                "service": "mcp-security-intel",
                "protocol": "MCP JSON-RPC 2.0",
                "port": PORT
            })
            return
        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path in ["/", "/mcp", "/jsonrpc"]:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            resp = handle_jsonrpc(body)
            self._send_json(200, resp)
            return
        self._send_json(404, {"error": "Endpoint not found"})

def main():
    server = HTTPServer(("0.0.0.0", PORT), MCPHTTPHandler)
    logging.info(f"External MCP Security Intelligence server listening on port {PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down external MCP server...")
        server.server_close()

if __name__ == "__main__":
    main()
