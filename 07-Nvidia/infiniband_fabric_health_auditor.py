#!/usr/bin/env python3
"""
NVIDIA InfiniBand & AI Ethernet Fabric Health Auditor
=====================================================
Part of: 07-Nvidia Systems & Low-Level Infrastructure Engineering Suite

Performs automated fabric telemetry audits by analyzing InfiniBand perfquery
counters, RoCE PFC pause frames, symbol errors, and credit stall metrics.
Detects dirty optical transceivers, downstream stragglers, and credit deadlocks.
"""

import sys
import argparse
from typing import Dict, Any, List

# Metric Thresholds based on NVIDIA Quantum NDR / XDR Engineering Specifications
THRESHOLDS = {
    "symbol_error_rate_crit": 1e-12,     # SerDes Bit Error Rate limit
    "symbol_error_rate_warn": 1e-14,
    "credit_stall_ratio_crit": 0.15,     # >15% ticks waiting for credits = severe congestion
    "credit_stall_ratio_warn": 0.03,     # >3% ticks waiting for credits = warning
    "link_down_crit": 1,                 # Any link drops in active production are fatal
    "packet_error_ratio_crit": 1e-6,
}

SAMPLE_TELEMETRY_DATA = [
    {
        "node_id": "dgx-gh200-node01",
        "hca": "mlx5_0",
        "port": 1,
        "speed": "NDR (400 Gbps)",
        "xmit_data_bytes": 100_000_000_000,
        "rcv_data_bytes": 98_000_000_000,
        "port_xmit_wait": 120,               # Minimal credit delay
        "symbol_error_counter": 0,           # Perfect optic
        "port_rcv_errors": 0,
        "link_downed_counter": 0,
        "link_recovery_counter": 0
    },
    {
        "node_id": "dgx-gh200-node02",
        "hca": "mlx5_1",
        "port": 1,
        "speed": "NDR (400 Gbps)",
        "xmit_data_bytes": 85_000_000_000,
        "rcv_data_bytes": 84_000_000_000,
        "port_xmit_wait": 45_000_000,        # Massive credit stall!
        "symbol_error_counter": 2,           # Optic is fine physically
        "port_rcv_errors": 0,
        "link_downed_counter": 0,
        "link_recovery_counter": 0
    },
    {
        "node_id": "dgx-b200-node08",
        "hca": "mlx5_3",
        "port": 1,
        "speed": "XDR (800 Gbps)",
        "xmit_data_bytes": 120_000_000_000,
        "rcv_data_bytes": 115_000_000_000,
        "port_xmit_wait": 300,
        "symbol_error_counter": 940_000,     # Huge symbol errors! Failing laser
        "port_rcv_errors": 18_450,           # Dropping corrupted frames
        "link_downed_counter": 3,            # Flapped 3 times
        "link_recovery_counter": 12
    }
]

def audit_port_health(telemetry: Dict[str, Any]) -> Dict[str, Any]:
    """Audit single port telemetry against physical fabric thresholds."""
    total_bits = (telemetry["rcv_data_bytes"] + telemetry["xmit_data_bytes"]) * 8
    symbol_errors = telemetry["symbol_error_counter"]
    rcv_errors = telemetry["port_rcv_errors"]
    xmit_wait = telemetry["port_xmit_wait"]
    link_downed = telemetry["link_downed_counter"]

    # Compute Bit Error Rate (BER) estimate
    ber = (symbol_errors / total_bits) if total_bits > 0 else 0.0

    # Approx stall ratio (relative to normalized 100M total ticks)
    stall_ratio = xmit_wait / 100_000_000.0

    diagnostics = []
    status = "HEALTHY"

    # Check 1: Optical / SerDes Physical Health
    if ber > THRESHOLDS["symbol_error_rate_crit"] or symbol_errors > 50_000:
        status = "CRITICAL"
        diagnostics.append(
            f"PHYSICAL LAYER DEGRADATION: Estimated BER ({ber:.2e}) exceeds critical limit ({THRESHOLDS['symbol_error_rate_crit']:.2e}). "
            f"Symbol errors: {symbol_errors}. Root cause: Dirty MPO fiber connector, cable micro-bending, or degraded optical transceiver laser."
        )
    elif ber > THRESHOLDS["symbol_error_rate_warn"]:
        if status != "CRITICAL":
            status = "WARNING"
        diagnostics.append(f"MARGINAL OPTICS: Elevated symbol errors ({symbol_errors}). Inspect optical transceiver RX power.")

    # Check 2: Congestion & Credit Deadlocks
    if stall_ratio > THRESHOLDS["credit_stall_ratio_crit"]:
        status = "CRITICAL"
        diagnostics.append(
            f"EXTREME FABRIC CREDIT STALL: PortXmitWait ({xmit_wait}) indicates sender was blocked for >15% of clock cycles waiting for receiver credits. "
            "Root cause: Downstream receiver bottleneck, collective communication straggler, or PFC pause deadlock."
        )
    elif stall_ratio > THRESHOLDS["credit_stall_ratio_warn"]:
        if status != "CRITICAL":
            status = "WARNING"
        diagnostics.append(f"MODERATE CONGESTION: Elevated PortXmitWait ({xmit_wait}). Monitor for slow node stragglers.")

    # Check 3: Link Stability / Flaps
    if link_downed >= THRESHOLDS["link_down_crit"]:
        status = "CRITICAL"
        diagnostics.append(
            f"LINK INSTABILITY / FLAPPING: Link downed {link_downed} times. Root cause: SerDes CDR loss of lock, loose latch, or PSU transient."
        )

    # Check 4: Packet Drops
    if rcv_errors > 0:
        if status != "CRITICAL":
            status = "WARNING"
        diagnostics.append(f"PACKET RECEPTION INTEGRITY: {rcv_errors} CRC/FCS packet drops detected.")

    return {
        "node_id": telemetry["node_id"],
        "hca": telemetry["hca"],
        "port": telemetry["port"],
        "speed": telemetry["speed"],
        "status": status,
        "ber": ber,
        "stall_ratio": stall_ratio,
        "diagnostics": diagnostics
    }

def print_audit_report(results: List[Dict[str, Any]]) -> int:
    """Print comprehensive fabric diagnostic report."""
    print("=" * 95)
    print("NVIDIA QUANTUM INFINIBAND & SPECTRUM-X FABRIC HEALTH AUDIT REPORT")
    print("=" * 95)

    critical_count = 0
    warning_count = 0

    for res in results:
        status_icon = "🟢" if res["status"] == "HEALTHY" else ("🟡" if res["status"] == "WARNING" else "🔴")
        print(f"\n{status_icon} NODE: {res['node_id']} | HCA: {res['hca']}:P{res['port']} [{res['speed']}] --> STATUS: {res['status']}")
        print(f"   • Physical SerDes BER : {res['ber']:.3e}")
        print(f"   • Credit Wait Ratio   : {res['stall_ratio'] * 100:.2f}%")

        if res["diagnostics"]:
            print("   ⚠️  Detected Anomalies:")
            for diag in res["diagnostics"]:
                print(f"      - {diag}")

            print("   🛠️ Recommended Action:")
            if "PHYSICAL" in "".join(res["diagnostics"]):
                print("      [1] Clean optical transceiver MPO endface with one-click cleaner.")
                print("      [2] Re-seat optical transceiver module into OSFP/QSFP cage.")
                print("      [3] Clear hardware counters: perfquery -C mlx5_0 -p 1 -r")
                print("      [4] If symbol errors recur, replace the optical transceiver.")
            elif "CREDIT" in "".join(res["diagnostics"]):
                print("      [1] Identify downstream destination node blocking credits via ibtracert.")
                print("      [2] Run nccl-tests all_reduce_perf to isolate the straggler rank.")
                print("      [3] Check if target node has thermal throttling or CPU unpinning.")
            elif "LINK INSTABILITY" in "".join(res["diagnostics"]):
                print("      [1] Check physical latch on OSFP connector.")
                print("      [2] Inspect switch-side port logs via UFM Enterprise or switch CLI.")

        if res["status"] == "CRITICAL":
            critical_count += 1
        elif res["status"] == "WARNING":
            warning_count += 1

    print("\n" + "=" * 95)
    print(f"AUDIT SUMMARY: {len(results)} Ports Analyzed | 🔴 Critical: {critical_count} | 🟡 Warning: {warning_count} | 🟢 Healthy: {len(results) - critical_count - warning_count}")
    print("=" * 95)

    return 2 if critical_count > 0 else (1 if warning_count > 0 else 0)

def main():
    parser = argparse.ArgumentParser(description="NVIDIA Fabric Health Auditor")
    parser.add_argument("--demo", action="store_true", help="Run with demonstration multi-node fabric telemetry")
    args = parser.parse_args()

    results = [audit_port_health(item) for item in SAMPLE_TELEMETRY_DATA]
    sys.exit(print_audit_report(results))

if __name__ == "__main__":
    main()
