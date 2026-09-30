#!/usr/bin/env bash
# Break/fix drills (Volume 19, 20). Inject a fault, diagnose it from symptoms,
# fix it yourself, then check with `verify`. `hint` and `answer` are there when stuck.
#   scripts/breakfix.sh list
#   scripts/breakfix.sh inject 06
#   scripts/breakfix.sh hint 06 | answer 06
#   scripts/breakfix.sh reset 06     # undo the injection (the "fix" if you gave up)
#   scripts/breakfix.sh reset all
source "$(dirname "$0")/lib.sh"
BF="$LAB_DIR/breakfix"
node() { k get nodes -o jsonpath='{.items[0].metadata.name}'; }

declare -A TITLE SYMPTOM HINT ANSWER
TITLE[01]="Quota: only some replicas start";          SYMPTOM[01]="deploy/bf01-big in tenant-alpha shows 2/3 ready, no third pod at all"
HINT[01]="Pods that are never created don't have events — the ReplicaSet does."
ANSWER[01]="kubectl -n tenant-alpha describe rs -l app=bf01 → 'exceeded quota: budget-5pct, requested: limits.cpu=500m, used: 1, limited: 1'. Fix: shrink requests/replicas or raise the quota (platform team)."
TITLE[02]="GPU slices exhausted";                      SYMPTOM[02]="some bf02-gpu pods Pending forever"
HINT[02]="Compare allocatable nvidia.com/gpu with what is already requested on the node."
ANSWER[02]="kubectl describe pod → '0/1 nodes are available: 1 Insufficient nvidia.com/gpu'. kubectl describe node | grep -A8 'Allocated resources'. Fix: fewer replicas, Kueue queueing, or more time-slice replicas (01 Ansible gpu_operator_timeslice_replicas) — which adds contention, not capacity."
TITLE[03]="Image pull failure";                        SYMPTOM[03]="pod bf03-pull never starts"
HINT[03]="Events on the pod say exactly what the kubelet asked the registry for."
ANSWER[03]="ErrImagePull → ImagePullBackOff, 'manifest unknown'. Check the tag in NGC; on arm64 also check the tag has an arm64 manifest: 'docker manifest inspect <img> | grep arm64'."
TITLE[04]="OOMKilled";                                 SYMPTOM[04]="bf04-oom restarts again and again"
HINT[04]="Look at lastState of the container, not the current state."
ANSWER[04]="kubectl get pod bf04-oom -o jsonpath='{.status.containerStatuses[0].lastState.terminated}' → reason OOMKilled, exitCode 137. memory.events oom_kill>0 (scripts/cgroup-inspect.sh). Fix: limit ≥ working set, or fix the leak."
TITLE[05]="Liveness probe kills a slow-loading model"; SYMPTOM[05]="bf05-slow in CrashLoopBackOff with restartCount climbing; logs look healthy"
HINT[05]="Who is killing it — the app, the OOM killer, or the kubelet?"
ANSWER[05]="Events: 'Liveness probe failed: HTTP probe failed with statuscode: 503' → 'Container api failed liveness probe, will be restarted'. The model takes 60 s; liveness gives 15 s. Fix: add a startupProbe (failureThreshold×period ≥ worst-case load time) — the pattern used in manifests/90-serving/vllm."
TITLE[06]="NetworkPolicy blocks the ingress controller"; SYMPTOM[06]="curl -H 'Host: llm.lab.local' http://<LB>/v1/models hangs then 504/502; pods are Ready"
HINT[06]="Pod-to-pod works inside llm-serving; from the ingress namespace it doesn't. What changed in llm-serving?"
ANSWER[06]="kubectl -n llm-serving get netpol → only bf06-default-deny is left; allow-ingress-and-monitoring is gone. NetworkPolicies are a union of allows: once a pod is selected by any Ingress policy, everything not explicitly allowed is dropped (by kube-router iptables on the node). Prove it: kubectl -n lab-tools exec deploy/netshoot -- curl -m3 mock-llm.llm-serving:8000/health times out. Fix: kubectl apply -k manifests/10-tenancy (restores the allow)."
TITLE[07]="Cluster DNS down";                          SYMPTOM[07]="new connections by name fail everywhere: 'could not resolve host', Python EAI_AGAIN"
HINT[07]="Test by IP and by name from netshoot. Which one fails?"
ANSWER[07]="kubectl -n kube-system get deploy coredns → 0/0. Fix: kubectl -n kube-system scale deploy coredns --replicas=1 (k3s would also restore it on restart). Prevention: PDB + PriorityClass system-cluster-critical (already set on CoreDNS)."
TITLE[08]="Service with no endpoints";                 SYMPTOM[08]="curl -H 'Host: bf08.lab.local' http://<LB>/ → 503 'no available server'"
HINT[08]="kubectl get endpointslices -l kubernetes.io/service-name=bf08-api"
ANSWER[08]="EndpointSlice has no endpoints: selector app=mock-lm matches nothing (typo). Fix: selector app=mock-llm. Lesson: 503 from ingress + Ready pods = selector/port mismatch until proven otherwise."
TITLE[09]="Streaming arrives in one blob";             SYMPTOM[09]="curl -N via bf09.lab.local prints nothing for seconds, then every token at once"
HINT[09]="Compare time-to-first-byte through bf09.lab.local and through llm.lab.local."
ANSWER[09]="curl -w '%{time_starttransfer}' shows TTFB ≈ total time. The bf09 Ingress has a Traefik 'buffering' middleware with maxResponseBodyBytes — it holds the whole response. Fix: never buffer responses on LLM routes (request-body limits only, as in llm-body-limit)."
TITLE[10]="GPU visible to a pod that didn't ask for one"; SYMPTOM[10]="bf10-leak logs list the GB10 although it requests no nvidia.com/gpu"
HINT[10]="Which container runtime ran this pod, and what does the image's NVIDIA_VISIBLE_DEVICES say?"
ANSWER[10]="k3s default-runtime is nvidia (01 Ansible) and CUDA images set NVIDIA_VISIBLE_DEVICES=all, so the runtime hook injects the GPU for every such pod. Tenant namespaces are protected by the VAP spark-no-nvidia-env-bypass only against *explicit* env; the image default still leaks. Fixes: (a) set k3s_cluster_default_runtime_nvidia=false and use runtimeClassName: nvidia for GPU pods, (b) NVIDIA device plugin with deviceListStrategy=volume-mounts + accept-nvidia-visible-devices-envvar-when-unprivileged=false in the toolkit config."
TITLE[11]="Drain blocked by a PodDisruptionBudget";   SYMPTOM[11]="kubectl drain spark-01 loops on 'Cannot evict pod as it would violate the pod's disruption budget'"
HINT[11]="kubectl get pdb -A — look at ALLOWED DISRUPTIONS."
ANSWER[11]="qdrant PDB maxUnavailable 0 with 1 replica → allowed disruptions 0. That's correct for a single copy of data. For planned maintenance: snapshot qdrant, then 'kubectl -n llm-serving delete pdb qdrant' or drain with --disable-eviction (bypasses PDB — you own the outage). Multi-node: run 3 replicas, PDB maxUnavailable 1."
TITLE[12]="PVC stuck Pending";                         SYMPTOM[12]="bf12-pod Pending, bf12-data Pending"
HINT[12]="Describe the PVC, not the pod."
ANSWER[12]="PVC event: 'storageclass.storage.k8s.io \"fast-nvme\" not found'. Fix: storageClassName: local-nvme (PVC spec is immutable → delete + recreate)."
TITLE[13]="Node tainted NoSchedule";                   SYMPTOM[13]="every new pod Pending: '1 node(s) had untolerated taint'"
HINT[13]="kubectl describe node | grep -i taint"
ANSWER[13]="Taint spark.lab/maintenance=true:NoSchedule. Fix: kubectl taint node <node> spark.lab/maintenance- . Existing pods kept running because NoSchedule doesn't evict (NoExecute would)."
TITLE[14]="Deployment creates no pods";                SYMPTOM[14]="deploy/bf14-latest 0/1, no pods, no pod events"
HINT[14]="kubectl -n tenant-beta describe rs -l app=bf14"
ANSWER[14]="ReplicaSet FailedCreate: \"ValidatingAdmissionPolicy 'spark-no-latest-tag' … denied request\". Fix: pin the image (registry.k8s.io/pause:3.10)."
TITLE[15]="Unified-memory pressure (RISKY)";           SYMPTOM[15]="node MemoryPressure, pods evicted, CUDA allocations failing in serving pods"
HINT[15]="kubectl describe node → Conditions; kubectl get events -A | grep -i evict; free -g on the host."
ANSWER[15]="bf15-uma grabbed ~92 % of MemAvailable. On UMA that is also GPU memory. kubelet evicts by QoS/usage once memory.available < eviction-hard (4Gi in 01 Ansible). Mitigations: realistic limits, PriorityClasses (serving outranks preemptible), vLLM --gpu-memory-utilization headroom, and 01 Ansible playbooks/24-uma-relief.yml (drop caches)."

inject() {
  case $1 in
    01|02|03|04|10|12|14|15) [[ $1 == 15 ]] && { read -r -p "BF-15 fills unified memory for ~2 min. Type 'yes': " a; [[ $a == yes ]] || exit 1; }
                             k apply -f "$BF/$1-"*.yaml ;;
    06) k -n llm-serving delete netpol allow-ingress-and-monitoring --ignore-not-found; k apply -f "$BF/06-"*.yaml ;;
    05|08|09) k apply -f "$BF/$1-"*.yaml ;;
    07) k -n kube-system scale deploy coredns --replicas=0 ;;
    11) info "apply 50-workloads first (qdrant + PDB), then run: kubectl drain $(node) --ignore-daemonsets --delete-emptydir-data --dry-run=server"
        k apply -k "$LAB_DIR/manifests/50-workloads" ;;
    13) k taint node "$(node)" spark.lab/maintenance=true:NoSchedule --overwrite ;;
    *) bad "unknown scenario $1"; exit 2 ;;
  esac
  echo; echo "Injected BF-$1: ${TITLE[$1]}"; echo "Symptom: ${SYMPTOM[$1]}"
}
reset() {
  case $1 in
    06) k delete -f "$BF/06-"*.yaml --ignore-not-found; k apply -k "$LAB_DIR/manifests/10-tenancy" >/dev/null ;;
    07) k -n kube-system scale deploy coredns --replicas=1 ;;
    11) info "nothing injected; uncordon if you drained: kubectl uncordon $(node)" ;;
    13) k taint node "$(node)" spark.lab/maintenance- 2>/dev/null || true ;;
    *) k delete -f "$BF/$1-"*.yaml --ignore-not-found --wait=false ;;
  esac
}

cmd=${1:-list}; id=${2:-}
case $cmd in
  list)   for i in $(printf '%s\n' "${!TITLE[@]}" | sort); do printf '  %s  %s\n' "$i" "${TITLE[$i]}"; done ;;
  inject) inject "$id" ;;
  hint)   echo "${HINT[$id]}" ;;
  answer) echo "${ANSWER[$id]}" ;;
  reset)  if [[ $id == all ]]; then for i in "${!TITLE[@]}"; do reset "$i" >/dev/null 2>&1; done; ok "all scenarios reset"; else reset "$id"; fi ;;
  *) echo "usage: $0 list|inject N|hint N|answer N|reset N|all"; exit 2 ;;
esac
