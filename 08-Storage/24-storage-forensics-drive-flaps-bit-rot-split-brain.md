# Volume 24: Storage Forensics — Drive Flaps, Bit-Rot, and Split-Brain Triage

```
====================================================================================================
MODULE 08: STORAGE & HIGH-PERFORMANCE DATA FABRIC FOR AI
VOLUME 24: PCIe Link Degradation, NVMe SMART Forensics, Hung Task D-States & Quorum Partitions
====================================================================================================
```

---

## 1. Executive Intuition: The "Gray Failure" Catastrophe

The most hazardous failures in modern AI storage fabrics are not hard crashes where a node goes dark. A hard crash is immediately detected by the cluster orchestrator (Slurm/Kubernetes), triggering node evacuation and job restart.

The catastrophic failures are **Gray Failures (Partial, Sub-Silent Degradations)**:
1. **PCIe Link Speed Flapping:** An NVMe drive or storage NIC running at PCIe Gen5 ($32\text{ GT/s}$, 16 lanes) suffers signal integrity degradation due to thermal expansion or connector dust, silently renegotiating down to PCIe Gen1 x1 ($2.5\text{ GT/s}$, 1 lane). Bandwidth collapses by $99.6\%$, throttling the entire cluster without throwing an OS error.
2. **Uninterruptible Sleep (`D-state`) Deadlocks:** DataLoader workers or checkpoint writers hang in kernel space on a deadlocked VFS inode lock or stale NFS/RDMA RPC. The processes ignore `kill -9`, leaking kernel resources and blocking GPU memory release.
3. **Split-Brain Quorum Divergence:** Network partitions between parallel file system metadata servers allow multiple isolated sub-clusters to accept conflicting mutations, causing permanent data corruption.

```
+-----------------------------------------------------------------------------------------+
|                              THE GRAY FAILURE SPECTRUM                                  |
+-----------------------------------------------------------------------------------------+
| Binary Failure (Clean):      [Node Dies] ------------> [Orchestrator Reschedules] (5 min)|
|                                                                                         |
| Gray Failure (Insidious):    [PCIe Degrades to Gen1]                                    |
|                              [Throughput: 63 GB/s -> 250 MB/s]                          |
|                              [1,024 GPUs stall at barrier waiting on slow rank]         |
|                              [Time to diagnose without forensics: 6 to 12 HOURS!]       |
+-----------------------------------------------------------------------------------------+
```

Resolving gray failures requires deep kernel-level forensics: **PCIe Advanced Error Reporting (AER)**, **NVMe SMART Telemetry Parsing**, **Kernel D-State Call Trace Analysis**, and **Fencing Mechanisms (STONITH)**.

---

## 2. Lineage & Evolution of Storage Forensics

```
   [1990s: SCSI Check Conditions]
                 |
           (SCSI Sense Keys, mechanical drive bad sector reallocation)
                 |
   [2003: PCI Express AER Specification]
                 |
           (PCI-SIG standardizes Advanced Error Reporting registers in config space)
                 |
   [2012: NVMe SMART / Health Logs]
                 |
           (Standardized NVMe controller log pages: Available Spare, Critical Warnings)
                 |
   [2018: Linux Hung Task Watchdog & BPF Sched]
                 |
           (Automated detection of tasks stuck in TASK_UNINTERRUPTIBLE for >120s)
                 |
   [2025: Autonomous Predictive Node Evacuation]
                 |
           (Automated forensic agents quarantining flapping drives before cluster stalls)
```

---

## 3. First-Principles Mathematics: PCIe Bus Degradation Collapse

PCIe bandwidth is determined by the signaling rate (Transfer rate in GigaTransfers/sec), the line encoding efficiency, and the physical lane count ($w$):

$$B_{\text{pcie}} = \text{Rate} \times \text{Encoding Efficiency} \times w$$

```
+-----------------------------------------------------------------------------------------+
|                         PCIe LINK SPECIFICATION DEGRADATION MATRIX                      |
+----------------------+--------------------+--------------------+------------------------+
| Generation & Width   | Transfer Rate      | Encoding Overhead  | Unidirectional Bandwidth|
+----------------------+--------------------+--------------------+------------------------+
| Gen5 x16 (Optimal)   | 32.0 GT/s          | 128b/130b (98.46%) | ~63.0 GB/sec           |
| Gen4 x16             | 16.0 GT/s          | 128b/130b (98.46%) | ~31.5 GB/sec           |
| Gen3 x16             | 8.0 GT/s           | 128b/130b (98.46%) | ~15.75 GB/sec          |
| Gen5 x4 (Degraded)   | 32.0 GT/s          | 128b/130b (98.46%) | ~15.75 GB/sec          |
| Gen1 x1 (Worst Case) | 2.5 GT/s           | 8b/10b (80.0%)     | ~0.25 GB/sec (250 MB/s)|
+----------------------+--------------------+--------------------+------------------------+
```

$$\text{Degradation Factor } (\text{Gen5 x16} \to \text{Gen1 x1}) = \frac{63.0}{0.25} = 252\times\text{ Throughput Collapse!}$$

A single NVMe drive or storage NIC that silently renegotiates down to Gen1 x1 reduces ingestion throughput from $63\text{ GB/s}$ to $250\text{ MB/s}$. An ingestion epoch that normally takes 2 minutes expands to over 8 hours!

---

## 4. Deep Architecture: Kernel D-State Hung Task Analysis

A process in `TASK_UNINTERRUPTIBLE` (`D-state`) cannot handle any signals (including `SIGKILL` / `kill -9`) because it is awaiting a synchronous hardware or kernel event.

```
+-----------------------------------------------------------------------------+
|                      D-STATE HUNG TASK ANATOMY                              |
+-----------------------------------------------------------------------------+
| PyTorch DataLoader Worker (PID: 140292)                                      |
|   |                                                                         |
|   | System call: read(fd, buf, 4096)                                        |
|   v                                                                         |
| Kernel VFS Layer (vfs_read)                                                 |
|   |                                                                         |
|   v                                                                         |
| File System Mutex Lock: xfs_ilock()                                         |
|   |                                                                         |
|   v                                                                         |
| Device Driver: Block I/O request submitted to NVMe Submission Queue         |
|   |                                                                         |
|   v                                                                         |
| io_schedule()  <--- Process put to sleep awaiting NVMe completion interrupt |
|   |                                                                         |
|   X (NVMe Controller firmware deadlocks / PCIe bus drops completion packet) |
|                                                                             |
| Result: Process remains permanently stuck in 'D' state.                     |
| Kernel Watchdog fires after 120 seconds:                                    |
| "echo 0 > /proc/sys/kernel/hung_task_timeout_secs"                          |
+-----------------------------------------------------------------------------+
```

### 4.1 Extracting the Kernel Call Stack
When a task is hung in D-state, inspect its kernel stack directly:

```bash
# Display exact kernel function where PID is blocked
cat /proc/140292/stack
```

*Example Output:*
```
[<0>] io_schedule+0x62/0x90
[<0>] do_read_cache_page+0x4c2/0x7b0
[<0>] read_cache_page+0x12/0x20
[<0>] nfs_readpage+0x6b/0x250 [nfs]
[<0>] generic_file_read_iter+0x82/0xd0
[<0>] vfs_read+0x242/0x310
[<0>] ksys_read+0x67/0xf0
[<0>] do_syscall_64+0x5b/0x90
[<0>] entry_SYSCALL_64_after_hwframe+0x63/0xcd
```
> **Diagnostic Analysis:** The worker is stuck inside `nfs_readpage`, indicating an unresponsive remote NFS storage target rather than local CPU contention.

---

## 5. Masterclass: PCIe Advanced Error Reporting (AER) Triage

PCIe AER categorizes hardware errors into two classes:
1. **Correctable Errors:** Handled transparently by hardware (e.g., replay of corrupted TLP packets via Link CRC). High rates indicate physical degradation.
2. **Uncorrectable Errors:**
   - **Non-Fatal:** The link remains operational, but specific transactions failed.
   - **Fatal:** The link is compromised (e.g., Surprise Down, Data Link Protocol Error).

```bash
# Inspect PCIe AER errors for NVMe device
lspci -vvv -s 0000:41:00.0 | grep -A 25 "Advanced Error Reporting"
```

*Critical AER Flags to Watch:*
- `Surprise Down+`: The drive physically detached or lost power across the bus.
- `Poisoned TLP+`: Corrupted data payload transmitted across PCIe.
- `Bad TLP+` / `Bad DLLP+`: High correctable error rate indicating damaged PCIe trace or gold finger connector oxidation.

---

## 6. Concrete Production Lab: Automated Storage Forensic Auditor

Below is a Python forensic tool that interrogates NVMe controllers, verifies PCIe negotiated link width/speed, scrapes kernel hung tasks, and flags silent hardware degradations.

```python
#!/usr/bin/env python3
"""
Production Lab: Automated Storage Forensic Auditor.
Inspects NVMe SMART telemetry, verifies PCIe generation/lane width,
and scans for kernel D-state hung tasks stalling distributed training.
"""

import subprocess
import re
import os
import sys

class StorageForensicAuditor:
    def __init__(self):
        pass

    def _run(self, cmd: list) -> str:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return res.stdout.strip()
        except Exception as e:
            return ""

    def audit_pcie_links(self) -> list:
        """Inspects all NVMe devices and checks for link speed/width degradation."""
        reports = []
        lspci_out = self._run(["lspci", "-D"])
        nvme_addresses = [
            line.split()[0] for line in lspci_out.splitlines()
            if "Non-Volatile memory controller" in line or "NVMe" in line
        ]

        for bdf in nvme_addresses:
            details = self._run(["lspci", "-vvv", "-s", bdf])
            cap_match = re.search(r'LnkCap:.*Speed\s+([\d\.]+GT/s),\s+Width\s+x(\d+)', details)
            sta_match = re.search(r'LnkSta:.*Speed\s+([\d\.]+GT/s),\s+Width\s+x(\d+)', details)

            if cap_match and sta_match:
                cap_speed, cap_width = cap_match.groups()
                sta_speed, sta_width = sta_match.groups()

                degraded = (cap_speed != sta_speed) or (int(sta_width) < int(cap_width))
                reports.append({
                    "bdf": bdf,
                    "capable": f"{cap_speed} x{cap_width}",
                    "current": f"{sta_speed} x{sta_width}",
                    "status": "DEGRADED (PCIe Flap/Drop!)" if degraded else "OPTIMAL"
                })
        return reports

    def audit_nvme_smart(self, device: str = "/dev/nvme0") -> dict:
        """Parses NVMe SMART log page for critical warnings and spare endurance."""
        out = self._run(["nvme", "smart-log", device])
        if not out:
            return {"error": f"Failed to read SMART log for {device}"}

        parsed = {}
        for line in out.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                parsed[k.strip()] = v.strip()

        avail_spare = int(parsed.get("available_spare", "100%").replace("%", ""))
        crit_warn = int(parsed.get("critical_warning", "0"), 0)
        media_errors = int(parsed.get("media_errors", "0").replace(",", ""))

        health = "HEALTHY"
        if crit_warn != 0 or avail_spare < 10 or media_errors > 0:
            health = "CRITICAL (Drive replacement required)"

        return {
            "device": device,
            "critical_warning": crit_warn,
            "available_spare_pct": avail_spare,
            "media_errors": media_errors,
            "overall_health": health
        }

    def scan_hung_d_states(self) -> list:
        """Scans /proc for processes stuck in uninterruptible sleep."""
        hung_procs = []
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    stat_path = os.path.join("/proc", pid, "stat")
                    with open(stat_path, "r") as f:
                        fields = f.read().split()
                    state = fields[2]
                    comm = fields[1]

                    if state == "D":
                        stack_path = os.path.join("/proc", pid, "stack")
                        stack = ""
                        if os.path.exists(stack_path):
                            with open(stack_path, "r") as sf:
                                stack = sf.readline().strip()
                        hung_procs.append({
                            "pid": int(pid),
                            "comm": comm,
                            "kernel_wait": stack
                        })
                except (IOError, ProcessLookupError):
                    continue
        return hung_procs

if __name__ == "__main__":
    auditor = StorageForensicAuditor()
    print("=== [1/3] Auditing PCIe Link Speeds & Degradations ===")
    pcie_results = auditor.audit_pcie_links()
    if pcie_results:
        for r in pcie_results:
            print(f"  [{r['bdf']}] Capable: {r['capable']} | Status: {r['status']}")
    else:
        print("  No physical NVMe PCIe devices detected (or non-root execution).")

    print("\n=== [2/3] Auditing Kernel D-State Hung Tasks ===")
    d_procs = auditor.scan_hung_d_states()
    if d_procs:
        for p in d_procs:
            print(f"  [ALERT] PID {p['pid']} ({p['comm']}) stuck in D-state! Wait: {p['kernel_wait']}")
    else:
        print("  Zero hung D-state tasks detected. System responsive.")
```

---

## 7. Comparative Diagnostic Matrix

| Failure Mode | Hardware / Kernel Signal | Impact on GPU Training | Definitive Forensic Command | Immediate Remediation |
| :--- | :--- | :--- | :--- | :--- |
| **PCIe Link Degradation** | `LnkSta: Speed 2.5GT/s, Width x1` | Ingestion bandwidth collapses $250\times$ | `lspci -vvv -s <bdf> \| grep LnkSta` | Reseat drive; replace riser card |
| **Media Bit-Rot** | NVMe `media_errors > 0`, AER Bad TLP | Checkpoint hash mismatch or `NaN` loss | `nvme smart-log /dev/nvmeX` | Evacuate node; RMA drive |
| **VFS D-State Deadlock** | `INFO: task blocked > 120s` in `dmesg` | Training stalls at step barrier | `cat /proc/<pid>/stack` | Trigger node hard reboot (`sysrq-b`) |
| **Split-Brain Partition** | Metadata cluster quorum logs error | File creations return `EROFS` / `EIO` | Check Raft/Paxos quorum status | Fence isolated partition via STONITH |

---

## 8. SRE Diagnostics & Troubleshooting Playbook

```
+---------------------------------------------------------------------------------------------------+
|                        STORAGE FORENSICS SRE DIAGNOSTIC MATRIX                                    |
+------------------------------------+--------------------------+-----------------------------------+
| Symptom / Failure Mode             | Root Cause Hypothesis    | Triage & Remediation Command      |
+------------------------------------+--------------------------+-----------------------------------+
| Training processes cannot be       | Process stuck in kernel  | Identify blocking kernel symbol:  |
| killed even with `kill -9`.        | `D-state` waiting on     | `cat /proc/<PID>/stack`           |
|                                    | unrecoverable hardware.  | Trigger Magic SysRq crashdump:    |
|                                    |                          | `echo c > /proc/sysrq-trigger`    |
+------------------------------------+--------------------------+-----------------------------------+
| NVMe drive randomly disappears     | PCIe Surprise Down:      | Inspect kernel ring buffer:       |
| from OS under heavy write load.    | Controller power sag     | `dmesg -T \| grep -i nvme`        |
|                                    | or thermal shutdown.     | Check chassis fan and PCIe power. |
+------------------------------------+--------------------------+-----------------------------------+
| Parallel filesystem mounts become  | Quorum loss: Network     | Verify cluster heartbeat:         |
| read-only simultaneously across    | split isolated metadata  | `weka cluster status` or          |
| 100 compute nodes.                 | leader nodes.            | `mmgetstate -a`                   |
+------------------------------------+--------------------------+-----------------------------------+
| High correctable PCIe error rates  | Dirty PCIe gold fingers  | Clean slot with compressed air;   |
| flooding `/var/log/messages`.      | or cracked solder ball   | replace NVMe carrier sled.        |
|                                    | on PCIe riser.           |                                   |
+------------------------------------+--------------------------+-----------------------------------+
```

---

## 9. Verification & Architectural Synthesis Checklist

- [ ] **PCIe Link Auditing Automated:** Automated startup script asserts all NVMe drives and NICs operate at full Gen5 x16 width and speed.
- [ ] **SMART Telemetry Alerting:** Alerts trigger if `available_spare < 10%` or `critical_warning != 0`.
- [ ] **D-State Hung Task Detection:** Kernel hung task timeout set to $120\text{ seconds}$ with automated alert dispatch.
- [ ] **AER Logging Verified:** Advanced Error Reporting enabled in BIOS/UEFI and monitored in Linux `dmesg`.
- [ ] **Automated Fencing (STONITH):** Unresponsive metadata or storage nodes fenced automatically to protect cluster data integrity.
