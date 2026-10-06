#!/usr/bin/env python3
"""
NVIDIA Cluster XID Forensic Analyzer & Multi-Dimensional Root-Cause Triager
===========================================================================
Part of: 07-Nvidia Systems & Low-Level Infrastructure Engineering Suite

Analyzes Linux kernel event buffers (dmesg), NVML telemetry dumps, and system logs
to classify GPU failure signatures, map 4-dimensional cross-layer root causes
(Compute, Interconnect, Electrical/Thermal, Kernel/OS), and generate actionable
remediation commands.
"""

import sys
import re
import argparse
from typing import Dict, List, Any, Optional

# Master XID Forensic Classification Database
XID_DATABASE: Dict[int, Dict[str, Any]] = {
    31: {
        "name": "GPU Memory Page Fault",
        "severity": "CRITICAL_APP",
        "layer": "Kernel / Compute",
        "description": "GPU MMU encountered an unmapped virtual address dereference or illegal memory access.",
        "common_causes": [
            "Out-of-bounds array access in CUDA kernel",
            "Dereferencing NULL or uninitialized device pointer",
            "UVM page fault on un-pinned host memory without ATS/PASID support",
            "Asynchronous race condition accessing freed cuMemMap allocation"
        ],
        "remediation": [
            "Run application under compute-sanitizer: compute-sanitizer --tool memcheck ./app",
            "Check for race conditions with: compute-sanitizer --tool racecheck ./app",
            "Enable CUDA_LAUNCH_BLOCKING=1 to pinpoint exact faulting kernel line",
            "Inspect UVM page fault telemetry: dcgmi profile -e 100,101 -s 1000"
        ]
    },
    43: {
        "name": "GPU Stopped Responding",
        "severity": "CRITICAL_DRIVER",
        "layer": "Compute / Kernel",
        "description": "Kernel driver command ring buffer timed out waiting for GPU microengine response.",
        "common_causes": [
            "Infinite loop inside CUDA kernel blocking SM warp schedulers",
            "GPU hardware lockup or microcode hang",
            "CUDA watchdog timer expiration on interactive display GPU"
        ],
        "remediation": [
            "Check active kernel threads using GDB CUDA: cuda-gdb ./app",
            "Reset GPU via nvidia-smi: nvidia-smi --gpu-reset -i <gpu_id>",
            "Inspect GSP firmware RPC status: dmesg -T | grep -E 'NVRM.*GSP'",
            "Verify watchdog status: nvidia-smi -q -d WATCHDOG"
        ]
    },
    45: {
        "name": "Preemptive Event / Bus Engine Timeout",
        "severity": "CRITICAL_BUS",
        "layer": "Interconnect / Bus",
        "description": "DMA copy engine or Host-to-Device FIFO queue timed out or stalled.",
        "common_causes": [
            "PCIe controller timeout during GPUDirect RDMA burst",
            "IOMMU context page table stall or PCIe AER uncorrectable error",
            "Pinned memory pinned buffer unmapped while DMA active"
        ],
        "remediation": [
            "Inspect PCIe AER error counters: lspci -vvv -s <pci_bus_id> | grep -A 10 'AER'",
            "Disable IOMMU PT mode or verify amd_iommu=on / intel_iommu=on",
            "Check dmesg for PCIe bus dropouts: dmesg -T | grep -E 'PCIe|AER'"
        ]
    },
    62: {
        "name": "Internal Microcode Parity / SRAM ECC Error",
        "severity": "FATAL_HARDWARE",
        "layer": "Compute / Silicon",
        "description": "Uncorrectable double-bit parity error detected in internal SM SRAM (Register File, L1/Shared Memory, or L2 Cache).",
        "common_causes": [
            "Physical silicon defect or SRAM gate degradation",
            "Cosmic ray / neutron particle single-event upset exceeding ECC parity capability",
            "Severe voltage ripple on Vcore rail during high-di/dt matrix execution"
        ],
        "remediation": [
            "Query uncorrectable ECC error locations: nvidia-smi -q -d ECC",
            "Execute Level 3 Hardware Validation: dcgmi diag -r 3",
            "Execute Level 4 Extended Burn-In: dcgmi diag -r 4",
            "If error persists across resets, RMA / physically replace the SXM/PCIe module immediately."
        ]
    },
    79: {
        "name": "GPU Fallen Off the Bus",
        "severity": "FATAL_INFRASTRUCTURE",
        "layer": "Electrical / Thermal / Interconnect",
        "description": "GPU failed to respond to PCIe/NVLink configuration read cycles. Physical link dropped.",
        "common_causes": [
            "Severe di/dt current step-load trip on 54V DC busbar causing PoL VRM under-voltage drop",
            "Thermal emergency shutdown (GPU temperature exceeded T_critical threshold ~95°C)",
            "Mechanical seating issue in blind-mate connector or riser card",
            "Facility power shelf rectifier drop or CDU cooling pump failure"
        ],
        "remediation": [
            "Query IPMI/BMC event log: ipmitool sel list | tail -n 20",
            "Check facility CDU and coolant supply temperature: ipmitool sensor | grep -E 'Inlet|Water|Temp'",
            "Verify 54V DC busbar voltage stability under load",
            "Check PCIe link state: lspci | grep -i nvidia",
            "Perform cold chassis power cycle (warm reboot cannot re-enumerate dropped PCIe/NVLink endpoints)"
        ]
    },
    92: {
        "name": "NVLink Hardware Link Failure / SerDes Lock Loss",
        "severity": "FATAL_FABRIC",
        "layer": "Interconnect / Fabric",
        "description": "High-speed NVLink differential SerDes receiver lost clock-data recovery (CDR) lock or exceeded hardware replay threshold.",
        "common_causes": [
            "PAM4 eye closure due to physical trace degradation or dirty blind-mate backplane pins",
            "NVSwitch ASIC internal buffer lockup or clock skew across switch trays",
            "Thermal throttling on NVSwitch tray or SXM baseboard SerDes receiver",
            "NVIDIA Fabric Manager daemon crash or routing table desynchronization"
        ],
        "remediation": [
            "Check NVLink error and replay counters: nvidia-smi nvlink -e",
            "Inspect physical NVLink status: nvidia-smi nvlink -s",
            "Verify Fabric Manager status: systemctl status nvidia-fabricmanager",
            "Restart Fabric Manager: systemctl restart nvidia-fabricmanager",
            "If CRC errors continuously increment, inspect blind-mate twinax cartridge or NVSwitch tray."
        ]
    }
}

DEMO_DMESG_LOGS = """
[  120.450123] NVRM: Xid (PCI:0000:0f:00.0): 31, pid=48921, name=python3, Chk: 0x00000001, Error: Page Fault in GPU MMU (VA=0x7f9a12400000)
[  340.112940] NVRM: Xid (PCI:0000:23:00.0): 92, pid=51200, name=nccl_allreduce, NVLink SerDes lock lost on Port 4 (Replay limit exceeded)
[  520.893101] NVRM: Xid (PCI:0000:85:00.0): 79, GPU has fallen off the bus! Device 0000:85:00.0 unreachable.
[  780.004122] NVRM: Xid (PCI:0000:a1:00.0): 62, pid=59124, name=cutlass_gemm, Uncorrectable SRAM parity error detected in SM 14 Sub-Core 2.
"""

def parse_xid_events(log_text: str) -> List[Dict[str, Any]]:
    """Parse raw kernel dmesg or syslog text for NVIDIA NVRM Xid events."""
    pattern = re.compile(r"NVRM:\s+Xid\s+\(PCI:([0-9a-fA-F:\.]+)\):\s+(\d+)(?:,\s+(.+))?")
    events = []
    for line in log_text.strip().split("\n"):
        match = pattern.search(line)
        if match:
            pci_id = match.group(1)
            xid = int(match.group(2))
            details = match.group(3) if match.group(3) else "No additional kernel details"
            events.append({
                "raw_line": line.strip(),
                "pci_id": pci_id,
                "xid": xid,
                "details": details
            })
    return events

def analyze_event(event: Dict[str, Any]) -> None:
    """Analyze a single XID event with multi-dimensional cross-layer triage."""
    xid = event["xid"]
    pci_id = event["pci_id"]
    meta = XID_DATABASE.get(xid, {
        "name": f"Unknown XID {xid}",
        "severity": "UNKNOWN",
        "layer": "General Driver",
        "description": "Unclassified or proprietary NVIDIA driver hardware interrupt.",
        "common_causes": ["Undocumented kernel trap or hardware warning"],
        "remediation": ["Collect full bug report: nvidia-bug-report.sh", "Check NVIDIA developer forums"]
    })

    print(f"\n{'=' * 90}")
    print(f"🚨 DETECTED FAILURE: XID {xid} - {meta['name']}")
    print(f"{'=' * 90}")
    print(f"• Target PCI Address : {pci_id}")
    print(f"• Severity Level     : {meta['severity']}")
    print(f"• Primary Subsystem  : {meta['layer']}")
    print(f"• Description        : {meta['description']}")
    print(f"• Log Details        : {event['details']}")
    print(f"\n🔍 Common Multi-Dimensional Root Causes:")
    for cause in meta["common_causes"]:
        print(f"   [-] {cause}")

    print(f"\n🛠️ Prescribed SRE Remediation Playbook:")
    for step_num, step in enumerate(meta["remediation"], 1):
        print(f"   [{step_num}] {step}")

def run_diagnostics(log_content: str) -> int:
    """Run full diagnostic scan on log content."""
    print("=" * 90)
    print("NVIDIA CLUSTER XID FORENSIC ANALYZER - SCANNING SYSTEM EVENT LOGS")
    print("=" * 90)
    events = parse_xid_events(log_content)
    if not events:
        print("✅ No NVIDIA NVRM XID events found. System kernel event logs are clean.")
        return 0

    print(f"⚠️  Found {len(events)} XID event(s) across accelerated computing nodes.")
    for ev in events:
        analyze_event(ev)

    print("\n" + "=" * 90)
    print("MULTI-DIMENSIONAL TRIAGE SUMMARY")
    print("=" * 90)
    severities = [XID_DATABASE.get(ev["xid"], {}).get("severity", "UNKNOWN") for ev in events]
    has_fatal = any("FATAL" in s for s in severities)
    if has_fatal:
        print("🔴 STATUS: FATAL INFRASTRUCTURE / HARDWARE CORRUPTION DETECTED.")
        print("   Action: Cordon node immediately. Drain Slurm / Kubernetes workloads to prevent stragglers.")
        return 2
    else:
        print("🟡 STATUS: APPLICATION / DRIVER LEVEL FAULTS DETECTED.")
        print("   Action: Isolate faulting application process; check memory bounds and code execution.")
        return 1

def main():
    parser = argparse.ArgumentParser(description="NVIDIA Cluster XID Forensic Analyzer")
    parser.add_argument("--file", "-f", help="Path to dmesg or syslog file")
    parser.add_argument("--demo", action="store_true", help="Run with demonstration simulated crash telemetry")
    args = parser.parse_args()

    if args.demo or not args.file:
        print("ℹ️ Running in DEMO MODE with multi-node crash telemetry:")
        sys.exit(run_diagnostics(DEMO_DMESG_LOGS))
    else:
        try:
            with open(args.file, "r") as f:
                content = f.read()
            sys.exit(run_diagnostics(content))
        except Exception as e:
            print(f"Error reading {args.file}: {e}", file=sys.stderr)
            sys.exit(3)

if __name__ == "__main__":
    main()
