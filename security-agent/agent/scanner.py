import subprocess
import os
import re
import json

def run_command(cmd, log_file=None):
    """Executes a command, logs it, and returns the output."""
    print(f"[EXEC] {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if log_file:
        with open(log_file, "a") as f:
            f.write(f"\n=== CMD: {' '.join(cmd)} ===\n")
            f.write(result.stdout)
            if result.stderr:
                f.write("\n=== STDERR ===\n" + result.stderr)
    return result

class SecurityScanner:
    def __init__(self, target, workspace_dir="/workspace", log_path="/workspace/security.log"):
        self.target = target
        self.workspace = workspace_dir
        self.scans_dir = os.path.join(workspace_dir, "scans")
        self.findings_dir = os.path.join(workspace_dir, "findings")
        self.log_path = log_path
        
        os.makedirs(self.scans_dir, exist_ok=True)
        os.makedirs(self.findings_dir, exist_ok=True)

    def log(self, message):
        line = f"[*] {message}"
        print(line)
        with open(self.log_path, "a") as f:
            f.write(line + "\n")

    def run_discovery(self):
        self.log(f"Running port discovery against {self.target}...")
        out_txt = os.path.join(self.scans_dir, "discovery.txt")
        out_xml = os.path.join(self.scans_dir, "discovery.xml")
        cmd = [
            "nmap", "-sT", "-Pn",
            "-p", "21,22,80,443,3306,8080,8443",
            "--open",
            "-oN", out_txt,
            "-oX", out_xml,
            self.target
        ]
        res = run_command(cmd, self.log_path)
        
        # Parse open ports
        open_ports = []
        for line in res.stdout.splitlines():
            if "/tcp" in line and "open" in line:
                open_ports.append(line.strip())
                self.log(f"  --> Found {line.strip()}")
        return open_ports

    def run_service_detection(self):
        self.log(f"Running service/version detection against {self.target}...")
        out_txt = os.path.join(self.scans_dir, "services.txt")
        out_xml = os.path.join(self.scans_dir, "services.xml")
        cmd = [
            "nmap", "-sT", "-Pn", "-sV",
            "-p", "22,80,3306,8080",
            "-oN", out_txt,
            "-oX", out_xml,
            self.target
        ]
        res = run_command(cmd, self.log_path)
        
        services = []
        for line in res.stdout.splitlines():
            if "/tcp" in line and ("open" in line or "filtered" in line):
                services.append(line.strip())
                self.log(f"  --> Service: {line.strip()}")
        return services

    def run_http_enumeration(self):
        self.log(f"Running HTTP headers and enumeration against {self.target}...")
        out_txt = os.path.join(self.scans_dir, "http.txt")
        cmd = [
            "nmap", "-sT", "-Pn",
            "-p", "80,8080",
            "--script", "http-title,http-headers,http-methods",
            "-oN", out_txt,
            self.target
        ]
        res = run_command(cmd, self.log_path)
        return res.stdout

    def collect_findings(self):
        self.log("Aggregating scan evidence into structured findings...")
        findings = {
            "target": self.target,
            "scans": {}
        }
        for scan_file in ["discovery.txt", "services.txt", "http.txt"]:
            p = os.path.join(self.scans_dir, scan_file)
            if os.path.exists(p):
                with open(p, "r") as f:
                    findings["scans"][scan_file] = f.read()

        findings_path = os.path.join(self.findings_dir, "findings.json")
        with open(findings_path, "w") as f:
            json.dump(findings, f, indent=2)
        
        self.log(f"Findings saved to {findings_path}")
        return findings
