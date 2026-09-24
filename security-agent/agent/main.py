import os
import sys
import time
from scanner import SecurityScanner
from analyzer import NemotronAnalyzer

def main():
    target = os.getenv("TARGET", "security-target.default.svc.cluster.local")
    workspace = os.getenv("WORKSPACE", "/workspace")
    log_file = os.path.join(workspace, "security.log")
    report_file = os.path.join(workspace, "report.md")

    # Ensure log file can be written immediately
    os.makedirs(workspace, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] === SECURITY ANALYZER INITIALIZING ===\n")

    scanner = SecurityScanner(target=target, workspace_dir=workspace, log_path=log_file)
    scanner.log(f"Starting autonomous security assessment...")
    scanner.log(f"Target system: {target}")
    scanner.log(f"Workspace mount: {workspace}")

    # Phase 1: Host and Port Discovery
    scanner.log("[1/4] Running Port Discovery...")
    open_ports = scanner.run_discovery()
    scanner.log(f"Discovered {len(open_ports)} open port(s)")

    # Phase 2: Service & Version Fingerprinting
    scanner.log("[2/4] Running Service & Version Detection...")
    services = scanner.run_service_detection()
    scanner.log(f"Detected {len(services)} active service fingerprint(s)")

    # Phase 3: HTTP Enumeration
    scanner.log("[3/4] Running HTTP & Web Component Enumeration...")
    scanner.run_http_enumeration()

    # Aggregate Evidence
    findings = scanner.collect_findings()

    # Phase 4: AI Analysis with Nemotron
    scanner.log("[4/4] Invoking NVIDIA Nemotron Security Assessment...")
    analyzer = NemotronAnalyzer()
    report_md = analyzer.analyze_findings(findings, log_fn=scanner.log)

    with open(report_file, "w") as f:
        f.write(report_md)

    scanner.log(f"Report generated successfully at {report_file}")
    scanner.log(f"=== SECURITY ANALYSIS RUN COMPLETED SUCCESSFULLY ===")

    # Keep runner alive in standby for live interaction, inspection, and suspend/resume
    print("[*] Security Analyzer entering active standby. Sandboxed task is ready for interaction.")
    sys.stdout.flush()
    while True:
        time.sleep(3600)

if __name__ == "__main__":
    main()
