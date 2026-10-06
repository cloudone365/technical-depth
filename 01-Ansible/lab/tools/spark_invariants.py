#!/usr/bin/env python3
"""
spark_invariants.py — run ON a DGX Spark (no Ansible needed) to check the
invariants the lab depends on. Good for: first boot, after a DGX OS update,
inside an incident, or as a Slurm/k8s node health probe.

  python3 spark_invariants.py            # human table
  python3 spark_invariants.py --json     # machine output
  python3 spark_invariants.py --peer 192.168.100.12   # also test fabric reachability

Exit code = number of FAILED checks (0 = healthy). WARN does not count.
"""
import argparse
import json
import os
import re
import shutil
import subprocess

T = 15
results = []


def sh(cmd, timeout=T):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def check(name, status, detail="", fix=""):
    results.append({"check": name, "status": status, "detail": detail, "fix": fix})


def c_platform():
    arch = os.uname().machine
    check("arch is aarch64", "PASS" if arch == "aarch64" else "FAIL", arch)
    n = os.cpu_count()
    check("20 CPU cores", "PASS" if n == 20 else "WARN", str(n),
          "offline cores? check `lscpu` and /sys/devices/system/cpu/online")
    rc, out, _ = sh(". /etc/os-release && echo $VERSION_ID")
    check("Ubuntu 24.04 base", "PASS" if out.startswith("24.") else "FAIL", out)
    if os.path.exists("/etc/dgx-release"):
        rc, out, _ = sh("grep -E 'DGX_(SWBUILD_VERSION|OTA_VERSION)' /etc/dgx-release | tail -1")
        check("DGX OS release file", "PASS", out)
    else:
        check("DGX OS release file", "WARN", "missing /etc/dgx-release", "not DGX OS, or image customised")


def c_gpu():
    if not shutil.which("nvidia-smi"):
        check("nvidia-smi present", "FAIL", "", "driver not installed / PATH")
        return
    rc, out, err = sh("nvidia-smi --query-gpu=name,driver_version,compute_cap --format=csv,noheader")
    if rc != 0:
        check("GPU answers nvidia-smi", "FAIL", err or f"rc={rc}",
              "see Chapter 29 Runbook A; check `dmesg | grep -i nvrm`")
        return
    name, drv, cc = [x.strip() for x in out.splitlines()[0].split(",")]
    check("GPU is GB10", "PASS" if "GB10" in name else "FAIL", name)
    check("driver >= 580", "PASS" if int(drv.split(".")[0]) >= 580 else "FAIL", drv)
    check("compute capability 12.1", "PASS" if cc == "12.1" else "WARN", cc,
          "build CUDA code with -gencode arch=compute_121,code=sm_121")
    rc, out, _ = sh("nvidia-smi | grep -o 'CUDA Version: [0-9.]*'")
    check("driver CUDA 13.x", "PASS" if "13." in out else "WARN", out)
    rc, out, _ = sh("journalctl -k --since '-24h' --no-pager | grep -c 'NVRM: Xid'")
    n = int(out or 0)
    check("no Xid in 24h", "PASS" if n == 0 else "WARN", f"{n} events",
          "journalctl -k | grep Xid ; Chapter 29 Runbook B")


def c_memory():
    info = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
    tot = info["MemTotal"] / 1048576
    avail = info["MemAvailable"] / 1048576
    cache = info.get("Cached", 0) / 1048576
    check("MemTotal >= 110 GiB", "PASS" if tot >= 110 else "FAIL", f"{tot:.1f} GiB")
    status = "PASS" if avail >= 16 else ("WARN" if avail >= 8 else "FAIL")
    check("UMA MemAvailable >= 16 GiB", status, f"{avail:.1f} GiB avail, {cache:.1f} GiB page cache",
          "stop idle model servers; `sync; echo 3 | sudo tee /proc/sys/vm/drop_caches`")


def c_runtime():
    rc, out, _ = sh("docker info --format '{{.DefaultRuntime}} {{json .Runtimes}}'")
    if rc != 0:
        check("docker reachable", "FAIL", "", "sudo systemctl status docker; user in docker group?")
        return
    check("docker default runtime nvidia", "PASS" if out.startswith("nvidia") else "WARN", out.split()[0],
          "ansible-playbook playbooks/11.1-containers.yml")
    check("CDI spec present",
          "PASS" if os.path.exists("/etc/cdi/nvidia.yaml") or os.path.exists("/var/run/cdi/nvidia.yaml") else "WARN",
          "", "sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml")


def c_fabric(peer):
    rc, out, _ = sh("ibdev2netdev")
    if rc != 0:
        check("ibdev2netdev", "WARN", "not available", "ships with DGX OS; fallback: `rdma link show`")
        return
    up = re.findall(r"(\S+) port \d+ ==> (\S+) \(Up\)", out)
    if not up:
        check("CX-7 link up", "WARN", "no port Up (single Spark?)", "check QSFP cable; reboot both nodes")
        return
    for rdma, netdev in up:
        try:
            speed = int(open(f"/sys/class/net/{netdev}/speed").read())
            mtu = int(open(f"/sys/class/net/{netdev}/mtu").read())
        except (OSError, ValueError):
            speed, mtu = -1, -1
        check(f"{netdev} 200G", "PASS" if speed == 200000 else "FAIL", f"{speed} Mb/s",
              "switch port: disable autoneg, force 200G (e.g. 200G-baseCR4)")
        check(f"{netdev} MTU 9000", "PASS" if mtu == 9000 else "WARN", str(mtu),
              "must match on both ends; ansible-playbook playbooks/13.1-fabric.yml")
        rc, st, _ = sh(f"ibv_devinfo -d {rdma} | awk '/state:/{{print $2; exit}}'")
        check(f"{rdma} PORT_ACTIVE", "PASS" if st == "PORT_ACTIVE" else "FAIL", st)
    if peer:
        rc, _, _ = sh(f"ping -c2 -W1 -M do -s 8972 {peer}")
        check(f"jumbo ping {peer}", "PASS" if rc == 0 else "FAIL", "",
              "MTU mismatch somewhere on the path, or wrong subnet/interface")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--peer")
    a = ap.parse_args()
    for fn in (c_platform, c_gpu, c_memory, c_runtime):
        fn()
    c_fabric(a.peer)
    fails = sum(r["status"] == "FAIL" for r in results)
    if a.json:
        print(json.dumps({"failed": fails, "results": results}, indent=2))
    else:
        w = max(len(r["check"]) for r in results)
        for r in results:
            line = f"[{r['status']:4}] {r['check']:<{w}}  {r['detail']}"
            if r["status"] != "PASS" and r["fix"]:
                line += f"\n        fix: {r['fix']}"
            print(line)
        print(f"\n{len(results)} checks, {fails} failed")
    raise SystemExit(fails)


if __name__ == "__main__":
    main()
