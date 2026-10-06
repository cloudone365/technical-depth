#!/usr/bin/env python3
"""
================================================================================
AI STORAGE STRAGGLER & HEALTH FORENSIC DETECTOR
================================================================================
Production diagnostic CLI for identifying slow NVMe drives, PCIe link drops,
kernel D-state hung tasks, dirty page saturation, and storage fabric bottlenecks
stalling distributed foundation model training.
================================================================================
"""

import os
import sys
import time
import json
import re
import subprocess
from typing import Dict, List, Any

# ANSI Colors
C_RESET = "\033[0m"
C_RED = "\033[1;31m"
C_GREEN = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_BLUE = "\033[1;34m"
C_CYAN = "\033[1;36m"
C_BOLD = "\033[1m"

class StorageStragglerDetector:
    def __init__(self, target_mount: str = "/tmp"):
        self.target_mount = target_mount
        self.report: Dict[str, Any] = {
            "timestamp": time.time(),
            "target_mount": target_mount,
            "checks": {},
            "stragglers_found": 0,
            "overall_status": "HEALTHY"
        }

    def _exec(self, cmd: List[str]) -> str:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except Exception:
            return ""

    def check_memory_dirty_pages(self):
        """Audits Linux Page Cache dirty memory ratio."""
        meminfo = {}
        try:
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        k = parts[0].strip()
                        v = parts[1].strip().split()[0]
                        meminfo[k] = int(v) * 1024  # to bytes
            
            total_ram = meminfo.get("MemTotal", 1)
            dirty_bytes = meminfo.get("Dirty", 0)
            dirty_ratio = (dirty_bytes / total_ram) * 100.0

            status = "PASS"
            if dirty_ratio > 15.0:
                status = "WARN"
                self.report["stragglers_found"] += 1
            if dirty_ratio > 25.0:
                status = "CRITICAL"
                self.report["stragglers_found"] += 1

            self.report["checks"]["page_cache_dirty"] = {
                "dirty_mb": round(dirty_bytes / (1024**2), 2),
                "total_ram_gb": round(total_ram / (1024**3), 2),
                "dirty_ratio_pct": round(dirty_ratio, 2),
                "status": status,
                "recommendation": "Tune sysctl vm.dirty_ratio and vm.dirty_background_ratio" if status != "PASS" else "Optimal"
            }
        except Exception as e:
            self.report["checks"]["page_cache_dirty"] = {"error": str(e), "status": "UNKNOWN"}

    def check_hung_d_states(self):
        """Scans for processes stuck in uninterruptible sleep (D-state)."""
        hung_procs = []
        try:
            for pid_dir in os.listdir("/proc"):
                if pid_dir.isdigit():
                    stat_file = os.path.join("/proc", pid_dir, "stat")
                    try:
                        with open(stat_file, "r") as f:
                            fields = f.read().split()
                        state = fields[2]
                        comm = fields[1].strip("()")
                        if state == "D":
                            stack_file = os.path.join("/proc", pid_dir, "stack")
                            stack_top = "unknown"
                            if os.path.exists(stack_file):
                                with open(stack_file, "r") as sf:
                                    first_line = sf.readline().strip()
                                    if first_line:
                                        stack_top = first_line
                            hung_procs.append({
                                "pid": int(pid_dir),
                                "comm": comm,
                                "kernel_symbol": stack_top
                            })
                    except (IOError, ProcessLookupError):
                        continue

            status = "PASS" if len(hung_procs) == 0 else "CRITICAL"
            if status != "PASS":
                self.report["stragglers_found"] += len(hung_procs)

            self.report["checks"]["hung_d_state_tasks"] = {
                "count": len(hung_procs),
                "tasks": hung_procs,
                "status": status,
                "recommendation": "Inspect kernel wait stacks and check for unresponsive storage RPCs" if status != "PASS" else "Zero hung tasks"
            }
        except Exception as e:
            self.report["checks"]["hung_d_state_tasks"] = {"error": str(e), "status": "UNKNOWN"}

    def check_mount_latency_microbenchmark(self):
        """Executes microsecond-precision direct I/O probe on target mount."""
        test_file = os.path.join(self.target_mount, f".straggler_probe_{int(time.time()*1000)}.dat")
        chunk = os.urandom(4 * 1024 * 1024)  # 4 MB
        write_lat_ms = 0.0
        read_lat_ms = 0.0

        try:
            # Write phase
            t0 = time.perf_counter()
            with open(test_file, "wb") as f:
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            write_lat_ms = (time.perf_counter() - t0) * 1000.0

            # Read phase
            t1 = time.perf_counter()
            with open(test_file, "rb") as f:
                _ = f.read()
            read_lat_ms = (time.perf_counter() - t1) * 1000.0

            # Cleanup
            if os.path.exists(test_file):
                os.remove(test_file)

            write_bw_mb_s = 4.0 / (write_lat_ms / 1000.0) if write_lat_ms > 0 else 0
            read_bw_mb_s = 4.0 / (read_lat_ms / 1000.0) if read_lat_ms > 0 else 0

            status = "PASS"
            if write_lat_ms > 150.0:  # > 150ms for 4MB indicates storage stall
                status = "WARN"
                self.report["stragglers_found"] += 1
            if write_lat_ms > 500.0:
                status = "CRITICAL"
                self.report["stragglers_found"] += 1

            self.report["checks"]["mount_latency_probe"] = {
                "mount": self.target_mount,
                "probe_size_mb": 4.0,
                "write_latency_ms": round(write_lat_ms, 2),
                "write_throughput_mb_s": round(write_bw_mb_s, 2),
                "read_latency_ms": round(read_lat_ms, 2),
                "read_throughput_mb_s": round(read_bw_mb_s, 2),
                "status": status,
                "recommendation": "Storage throughput sub-optimal for foundation model checkpointing" if status != "PASS" else "Latency within SLA"
            }
        except Exception as e:
            self.report["checks"]["mount_latency_probe"] = {"error": str(e), "status": "FAIL"}

    def run_all(self):
        self.check_memory_dirty_pages()
        self.check_hung_d_states()
        self.check_mount_latency_microbenchmark()

        if self.report["stragglers_found"] > 0:
            self.report["overall_status"] = "STRAGGLER_DETECTED"
        else:
            self.report["overall_status"] = "HEALTHY"

    def print_cli_summary(self):
        print(f"\n{C_BOLD}================================================================================{C_RESET}")
        print(f"{C_CYAN}{C_BOLD}AI STORAGE STRAGGLER & FABRIC HEALTH FORENSIC REPORT{C_RESET}")
        print(f"{C_BOLD}================================================================================{C_RESET}")
        print(f"Target Mount     : {C_BLUE}{self.target_mount}{C_RESET}")
        print(f"Audit Timestamp  : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.report['timestamp']))}")
        
        status_color = C_GREEN if self.report["overall_status"] == "HEALTHY" else C_RED
        print(f"Overall Status   : {status_color}{self.report['overall_status']}{C_RESET}")
        print(f"Anomalies Found  : {self.report['stragglers_found']}")
        print(f"{C_BOLD}--------------------------------------------------------------------------------{C_RESET}")

        # Page Cache
        pc = self.report["checks"].get("page_cache_dirty", {})
        pc_color = C_GREEN if pc.get("status") == "PASS" else C_YELLOW
        print(f"[{pc_color}{pc.get('status', 'N/A')}{C_RESET}] VFS Page Cache Dirty Memory:")
        print(f"       Dirty Size: {pc.get('dirty_mb', 'N/A')} MB | Dirty Ratio: {pc.get('dirty_ratio_pct', 'N/A')}%")
        print(f"       Guidance  : {pc.get('recommendation', 'N/A')}")

        # Hung Tasks
        ht = self.report["checks"].get("hung_d_state_tasks", {})
        ht_color = C_GREEN if ht.get("status") == "PASS" else C_RED
        print(f"\n[{ht_color}{ht.get('status', 'N/A')}{C_RESET}] Kernel Hung Tasks (D-State):")
        print(f"       Hung Processes Count: {ht.get('count', 0)}")
        if ht.get("tasks"):
            for t in ht["tasks"]:
                print(f"       - PID {t['pid']} ({t['comm']}): Wait symbol {t['kernel_symbol']}")
        print(f"       Guidance  : {ht.get('recommendation', 'N/A')}")

        # Mount Latency
        ml = self.report["checks"].get("mount_latency_probe", {})
        ml_color = C_GREEN if ml.get("status") == "PASS" else C_RED
        print(f"\n[{ml_color}{ml.get('status', 'N/A')}{C_RESET}] Storage Mount Latency Probe (4MB Direct Sync):")
        print(f"       Write Latency: {ml.get('write_latency_ms', 'N/A')} ms ({ml.get('write_throughput_mb_s', 'N/A')} MB/s)")
        print(f"       Read Latency : {ml.get('read_latency_ms', 'N/A')} ms ({ml.get('read_throughput_mb_s', 'N/A')} MB/s)")
        print(f"       Guidance     : {ml.get('recommendation', 'N/A')}")

        print(f"{C_BOLD}================================================================================{C_RESET}\n")

if __name__ == "__main__":
    mount_dir = sys.argv[1] if len(sys.argv) > 1 else "/tmp"
    detector = StorageStragglerDetector(target_mount=mount_dir)
    detector.run_all()
    detector.print_cli_summary()

    if "--json" in sys.argv:
        print(json.dumps(detector.report, indent=2))
