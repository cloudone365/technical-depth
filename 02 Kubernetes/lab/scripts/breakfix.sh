#!/usr/bin/env bash
# Break/fix drills (Volume 19, 20, 27). Inject a fault, diagnose it from symptoms,
# fix it yourself, then check with `verify`. `hint` and `answer` are there when stuck.
# Each scenario names the cluster it breaks: spark-root, dev-lab or llms.
#   scripts/breakfix.sh list
#   scripts/breakfix.sh inject 06
#   scripts/breakfix.sh hint 06 | answer 06
#   scripts/breakfix.sh reset 06     # undo the injection (the "fix" if you gave up)
#   scripts/breakfix.sh reset all
source "$(dirname "$0")/lib.sh"
BF="$LAB_DIR/breakfix"
node() { kr get nodes -o jsonpath='{.items[0].metadata.name}'; }

declare -A TITLE CTX SYMPTOM HINT ANSWER
TITLE[01]="Quota: only some replicas start";          CTX[01]=dev-lab
SYMPTOM[01]="deploy/bf01-big in tenant-alpha (dev-lab) shows 2/3 ready, no third pod at all"
HINT[01]="Pods that are never created don't have events — the ReplicaSet does. Which API server refused it?"
ANSWER[01]="kubectl --context dev-lab -n tenant-alpha describe rs -l app=bf01 → 'exceeded quota: tenant-budget, requested: limits.cpu=250m, used: 500m, limited: 500m'. dev-lab's own API server refused it — the tenant quota inside the vCluster. Fix: shrink requests/replicas or raise the quota (platform team, in dev-lab/10-tenancy)."
TITLE[02]="Pending in the vCluster, nothing on the root"; CTX[02]=dev-lab
SYMPTOM[02]="4 of 6 bf02-gpu pods Pending in dev-lab forever; kubectl describe shows no scheduler events at all"
HINT[02]="The node has 15 slices. Look at the events of the Pending pod in dev-lab, then look for the pod on the root (vc-dev-lab)."
ANSWER[02]="kubectl --context dev-lab -n lab-tools describe pod <pending> → events from the vCluster syncer: 'exceeded quota: vcluster-budget, requested: requests.nvidia.com/gpu=1, used: 2, limited: 2'. The pod exists only in dev-lab; the ROOT quota on vc-dev-lab refused to create it on the host, so the root scheduler never saw it. Confirm: kubectl --context spark-root -n vc-dev-lab describe resourcequota vcluster-budget. Fix: fewer replicas, Kueue, or move GPU budget from llms to dev-lab (root admin edits root/05-vclusters/quotas.yaml)."
TITLE[03]="Image pull failure";                        CTX[03]=dev-lab
SYMPTOM[03]="pod bf03-pull (dev-lab, lab-tools) never starts"
HINT[03]="Events on the pod say exactly what the kubelet asked the registry for — vCluster copies them from the root."
ANSWER[03]="ErrImagePull → ImagePullBackOff, 'manifest unknown'. Check the tag in NGC; on arm64 also check the tag has an arm64 manifest: 'docker manifest inspect <img> | grep arm64'. The kubelet on the root pulled; the events you read in dev-lab are synced from the root pod."
TITLE[04]="OOMKilled";                                 CTX[04]=dev-lab
SYMPTOM[04]="bf04-oom (dev-lab, lab-tools) restarts again and again"
HINT[04]="Look at lastState of the container, not the current state."
ANSWER[04]="kubectl --context dev-lab -n lab-tools get pod bf04-oom -o jsonpath='{.status.containerStatuses[0].lastState.terminated}' → reason OOMKilled, exitCode 137. memory.events oom_kill>0 (scripts/cgroup-inspect.sh lab-tools bf04-oom dev-lab — it finds the host pod for you). Fix: limit ≥ working set, or fix the leak."
TITLE[05]="Liveness probe kills a slow-loading model"; CTX[05]=llms
SYMPTOM[05]="bf05-slow (llms, llm-serving) in CrashLoopBackOff with restartCount climbing; logs look healthy"
HINT[05]="Who is killing it — the app, the OOM killer, or the kubelet?"
ANSWER[05]="Events: 'Liveness probe failed: HTTP probe failed with statuscode: 503' → 'Container api failed liveness probe, will be restarted'. The model takes 60 s; liveness gives 15 s. Fix: add a startupProbe (failureThreshold×period ≥ worst-case load time) — the pattern used in manifests/llms/90-serving/vllm."
TITLE[06]="NetworkPolicy blocks the ingress controller"; CTX[06]=llms
SYMPTOM[06]="curl -H 'Host: llm.lab.local' http://192.168.0.115/v1/models hangs then 504/502; pods are Ready"
HINT[06]="Pod-to-pod works inside llm-serving; from the ingress namespace it doesn't. What changed in llm-serving — and what does Hubble say?"
ANSWER[06]="kubectl --context llms -n llm-serving get netpol → only bf06-default-deny is left; allow-ingress-and-same-namespace is gone. NetworkPolicies are a union of allows: once a pod is selected by any Ingress policy, everything not explicitly allowed is dropped. vCluster synced the policy to vc-llms and Cilium enforces it on the node: kubectl --context spark-root -n kube-system exec ds/cilium -- hubble observe --verdict DROPPED --last 20 shows Traefik → mock-llm drops. Fix: kubectl --context llms apply -k manifests/llms/10-tenancy (restores the allow)."
TITLE[07]="Root cluster DNS down";                     CTX[07]=spark-root
SYMPTOM[07]="platform pods can't resolve names: Grafana alert notifications fail, 'could not resolve host' from platform-tools/netshoot-host"
HINT[07]="Test by IP and by name from netshoot-host on the root. Then try the same from netshoot in dev-lab — what is different?"
ANSWER[07]="kubectl --context spark-root -n kube-system get deploy coredns → 0/0. Fix: kubectl --context spark-root -n kube-system scale deploy coredns --replicas=2. Note what kept working: cluster names INSIDE a vCluster are answered by that vCluster's own CoreDNS, so tenants may not notice at first — a shared failure with a delayed, uneven blast radius. Prevention: a PodDisruptionBudget for CoreDNS (kubeadm does not create one — write it) and PriorityClass system-cluster-critical (kubeadm already sets that)."
TITLE[08]="Service with no endpoints";                 CTX[08]=llms
SYMPTOM[08]="curl -H 'Host: bf08.lab.local' http://192.168.0.115/ → 503 'no available server'"
HINT[08]="kubectl --context llms -n llm-serving get endpointslices -l kubernetes.io/service-name=bf08-api"
ANSWER[08]="EndpointSlice has no endpoints: selector app=mock-lm matches nothing (typo). Fix: selector app=mock-llm. Lesson: 503 from ingress + Ready pods = selector/port mismatch until proven otherwise."
TITLE[09]="Streaming arrives in one blob";             CTX[09]=llms
SYMPTOM[09]="curl -N via bf09.lab.local prints nothing for seconds, then every token at once"
HINT[09]="Compare time-to-first-byte through bf09.lab.local and through llm.lab.local."
ANSWER[09]="curl -w '%{time_starttransfer}' shows TTFB ≈ total time. The bf09 Ingress has a Traefik 'buffering' middleware with maxResponseBodyBytes — it holds the whole response. Fix: never buffer responses on LLM routes (request-body limits only, as in llm-body-limit)."
TITLE[10]="GPU visible to a pod that didn't ask for one"; CTX[10]=dev-lab
SYMPTOM[10]="bf10-leak (dev-lab) logs list the GB10 although it requests no nvidia.com/gpu — and dev-lab's GPU quota doesn't count it"
HINT[10]="Which container runtime ran this pod on the root, and what does the image's NVIDIA_VISIBLE_DEVICES say?"
ANSWER[10]="containerd's default runtime is nvidia (01 Ansible kubeadm_cluster_default_runtime_nvidia) and CUDA images set NVIDIA_VISIBLE_DEVICES=all, so the runtime hook injects the GPU for every such pod — in any vCluster, outside every quota. The VAP spark-no-nvidia-env-bypass only blocks *explicit* env; the image default still leaks. Fixes: (a) kubeadm_cluster_default_runtime_nvidia=false and runtimeClassName: nvidia for GPU pods, (b) NVIDIA device plugin with deviceListStrategy=volume-mounts + accept-nvidia-visible-devices-envvar-when-unprivileged=false in the toolkit config."
TITLE[11]="Drain blocked by a PodDisruptionBudget";   CTX[11]=spark-root
SYMPTOM[11]="kubectl --context spark-root drain dgx-spark-01 loops on 'Cannot evict pod as it would violate the pod's disruption budget'"
HINT[11]="kubectl --context spark-root get pdb -A — whose PDB is that? Where was it written?"
ANSWER[11]="The PDB is qdrant's, written INSIDE llms and synced to vc-llms (sync.toHost.podDisruptionBudgets). maxUnavailable 0 with 1 replica → allowed disruptions 0. That's correct for a single copy of data, and it is why PDB sync is on: without it a root drain would silently evict tenant data. For planned maintenance: snapshot qdrant, then 'kubectl --context llms -n llm-serving delete pdb qdrant' or drain with --disable-eviction (bypasses PDB — you own the outage). Multi-node: 3 replicas, PDB maxUnavailable 1."
TITLE[12]="PVC stuck Pending";                         CTX[12]=dev-lab
SYMPTOM[12]="bf12-pod Pending, bf12-data Pending (dev-lab, lab-tools)"
HINT[12]="Describe the PVC, not the pod."
ANSWER[12]="PVC event (synced from the root's copy in vc-dev-lab): 'storageclass.storage.k8s.io \"fast-nvme\" not found'. kubectl --context dev-lab get sc lists the classes the root offers (local-path, local-nvme, local-nvme-retain). Fix: storageClassName: local-nvme (PVC spec is immutable → delete + recreate)."
TITLE[13]="Node tainted NoSchedule";                   CTX[13]=spark-root
SYMPTOM[13]="every new pod Pending — in the root AND in both vClusters: '1 node(s) had untolerated taint'"
HINT[13]="kubectl --context spark-root describe node | grep -i taint"
ANSWER[13]="Taint spark.lab/maintenance=true:NoSchedule on the only node. One node, one scheduler: all three clusters stop at once. Fix: kubectl --context spark-root taint node <node> spark.lab/maintenance- . Existing pods kept running because NoSchedule doesn't evict (NoExecute would)."
TITLE[14]="Deployment creates no pods";                CTX[14]=dev-lab
SYMPTOM[14]="deploy/bf14-latest 0/1 in tenant-beta (dev-lab), no pods, no pod events"
HINT[14]="kubectl --context dev-lab -n tenant-beta describe rs -l app=bf14"
ANSWER[14]="ReplicaSet FailedCreate: \"ValidatingAdmissionPolicy 'spark-no-latest-tag' … denied request\" — enforced by dev-lab's own API server. Fix: pin the image (registry.k8s.io/pause:3.10)."
TITLE[15]="Unified-memory pressure (RISKY)";           CTX[15]=spark-root
SYMPTOM[15]="node MemoryPressure, pods evicted in every cluster, CUDA allocations failing in llms serving pods"
HINT[15]="kubectl --context spark-root describe node → Conditions; kubectl get events -A | grep -i evict; free -g on the host."
ANSWER[15]="bf15-uma (root, platform-tools) grabbed ~92 % of MemAvailable. On UMA that is also GPU memory — and the vCluster memory budgets are quotas, not reservations, so they protect nothing against a platform pod. kubelet evicts by QoS/usage once memory.available < eviction-hard (4Gi in 01 Ansible). Mitigations: realistic limits, PriorityClasses (serving outranks preemptible), vLLM --gpu-memory-utilization headroom, and 01 Ansible playbooks/24-uma-relief.yml (drop caches)."

inject() {
  local c=${CTX[$1]:-}
  [[ -n "$c" ]] || { bad "unknown scenario $1"; exit 2; }
  case $1 in
    01|02|03|04|10|12|14|15)
      [[ $1 == 15 ]] && { read -r -p "BF-15 fills unified memory for ~2 min. Type 'yes': " a; [[ $a == yes ]] || exit 1; }
      kc "$c" apply -f "$BF/$1-"*.yaml ;;
    06) kl -n llm-serving delete netpol allow-ingress-and-same-namespace --ignore-not-found; kl apply -f "$BF/06-"*.yaml ;;
    05|08|09) kl apply -f "$BF/$1-"*.yaml ;;
    07) kr -n kube-system scale deploy coredns --replicas=0 ;;
    11) info "apply llms/50-workloads first (qdrant + PDB), then run: kubectl --context $ROOT_CTX drain $(node) --ignore-daemonsets --delete-emptydir-data --dry-run=server"
        kl apply -k "$LAB_DIR/manifests/llms/50-workloads" ;;
    13) kr taint node "$(node)" spark.lab/maintenance=true:NoSchedule --overwrite ;;
  esac
  echo; echo "Injected BF-$1 [$c]: ${TITLE[$1]}"; echo "Symptom: ${SYMPTOM[$1]}"
}
reset() {
  local c=${CTX[$1]:-$ROOT_CTX}
  case $1 in
    06) kl delete -f "$BF/06-"*.yaml --ignore-not-found; kl apply -k "$LAB_DIR/manifests/llms/10-tenancy" >/dev/null ;;
    07) kr -n kube-system scale deploy coredns --replicas=2 ;;
    11) info "nothing injected; uncordon if you drained: kubectl --context $ROOT_CTX uncordon $(node)" ;;
    13) kr taint node "$(node)" spark.lab/maintenance- 2>/dev/null || true ;;
    *) kc "$c" delete -f "$BF/$1-"*.yaml --ignore-not-found --wait=false ;;
  esac
}

cmd=${1:-list}; id=${2:-}
case $cmd in
  list)   for i in $(printf '%s\n' "${!TITLE[@]}" | sort); do printf '  %s  %-10s %s\n' "$i" "${CTX[$i]}" "${TITLE[$i]}"; done ;;
  inject) inject "$id" ;;
  hint)   echo "${HINT[$id]}" ;;
  answer) echo "${ANSWER[$id]}" ;;
  reset)  if [[ $id == all ]]; then for i in "${!TITLE[@]}"; do reset "$i" >/dev/null 2>&1; done; ok "all scenarios reset"; else reset "$id"; fi ;;
  *) echo "usage: $0 list|inject N|hint N|answer N|reset N|all"; exit 2 ;;
esac
