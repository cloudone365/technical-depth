#!/usr/bin/env bash
# Drive the 3-member sandbox etcd (lab/etcd-sandbox/compose.yaml) — Chapter 02.
#   etcd-sandbox.sh up|down|status
#   etcd-sandbox.sh kill-leader        # watch a new election (quorum 2/3 survives)
#   etcd-sandbox.sh kill-two           # lose quorum: reads OK (serializable), writes fail
#   etcd-sandbox.sh fill               # write until NOSPACE alarm (32 MiB quota)
#   etcd-sandbox.sh recover-space      # compact → defrag → disarm
source "$(dirname "$0")/lib.sh"
cd "$LAB_DIR/etcd-sandbox" || exit 1
E=(docker compose exec -T cli etcdctl)            # ETCDCTL_ENDPOINTS = all 3 members
E1=(docker compose exec -T cli etcdctl --endpoints=http://e1:2379)
leader() { "${E[@]}" endpoint status -w json 2>/dev/null | python3 -c 'import json,sys
for e in json.load(sys.stdin):
    if e["Status"]["leader"] == e["Status"]["header"]["member_id"]: print(e["Endpoint"].split("//")[1].split(":")[0])'; }
case "${1:-status}" in
  up)     docker compose up -d; sleep 3; "$0" status ;;
  down)   docker compose down -v ;;
  status) "${E[@]}" endpoint status -w table; "${E[@]}" alarm list ;;
  kill-leader)
    l=$(leader); info "stopping leader $l"; t0=$(date +%s%N); docker compose stop "$l" >/dev/null
    until n=$(leader) && [[ -n "$n" && "$n" != "$l" ]]; do sleep 0.1; done
    ok "new leader $n after $(( ($(date +%s%N)-t0)/1000000 )) ms (election timeout 1000 ms)"
    "${E[@]}" put /after-failover ok && ok "writes still work with 2/3 members"
    docker compose start "$l" >/dev/null ;;
  kill-two)
    [[ "$(leader)" == e1 ]] || info "leader is $(leader); stopping e2+e3 anyway"
    docker compose stop e2 e3 >/dev/null
    "${E1[@]}" --command-timeout=3s put /x y \
      && bad "write succeeded without quorum?!" || ok "write failed: no quorum (expected)"
    "${E1[@]}" get /after-failover --consistency=s \
      && ok "serializable (possibly stale) read still served locally"
    docker compose start e2 e3 >/dev/null ;;
  fill)
    blob=$(head -c 65536 /dev/urandom | base64 -w0)
    i=0; while "${E[@]}" put "/fill/$((i%50))" "$blob" >/dev/null 2>&1; do i=$((i+1)); done
    warn "stopped after $i puts"; "${E[@]}" alarm list; "${E[@]}" endpoint status -w table ;;
  recover-space)
    rev=$("${E[@]}" endpoint status -w json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["Status"]["header"]["revision"])')
    "${E[@]}" compact "$rev"
    "${E[@]}" defrag --cluster
    "${E[@]}" alarm disarm
    "${E[@]}" endpoint status -w table ;;
  *) echo "usage: $0 up|down|status|kill-leader|kill-two|fill|recover-space"; exit 2 ;;
esac
