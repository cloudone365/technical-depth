#!/usr/bin/env python3
"""Sync secrets from HashiCorp Vault (KV v2) into Kubernetes Secrets (Volume 32).

Runs as a CronJob with its own ServiceAccount:
  1. log in to Vault with the Kubernetes auth method (the pod's SA token — no static Vault credentials)
  2. read each mapped KV v2 path
  3. create or replace the target Kubernetes Secret through the API server
Stdlib only. Config (env):
  VAULT_ADDR      https://10.10.10.11:8200          VAULT_CACERT  /vault-ca/ca.crt
  VAULT_ROLE      deepseek-serving                   VAULT_AUTH_PATH kubernetes
  SYNC_MAP        JSON: [{"vault": "kv/data/spark-lab/deepseek/hf", "secret": "hf-token", "keys": {"token": "token"}}]
"""
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

SA = "/var/run/secrets/kubernetes.io/serviceaccount"


def req(url, method="GET", body=None, headers=None, ctx=None):
    r = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(r, context=ctx, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


def main():
    vault = os.environ.get("VAULT_ADDR", "https://10.10.10.11:8200")
    vctx = ssl.create_default_context(cafile=os.environ.get("VAULT_CACERT", "/vault-ca/ca.crt"))
    kctx = ssl.create_default_context(cafile=f"{SA}/ca.crt")
    jwt = open(f"{SA}/token").read().strip()
    ns = open(f"{SA}/namespace").read().strip()
    login = req(f"{vault}/v1/auth/{os.environ.get('VAULT_AUTH_PATH', 'kubernetes')}/login", "POST",
                {"role": os.environ.get("VAULT_ROLE", "deepseek-serving"), "jwt": jwt}, ctx=vctx)
    vtoken = login["auth"]["client_token"]
    print(f"vault login ok: policies={login['auth']['policies']} ttl={login['auth']['lease_duration']}s")
    api = "https://kubernetes.default.svc"
    kh = {"Authorization": f"Bearer {jwt}"}
    changed = 0
    for m in json.loads(os.environ["SYNC_MAP"]):
        data = req(f"{vault}/v1/{m['vault']}", headers={"X-Vault-Token": vtoken}, ctx=vctx)["data"]["data"]
        sec = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
               "metadata": {"name": m["secret"], "namespace": ns,
                            "labels": {"app.kubernetes.io/managed-by": "vault-sync"},
                            "annotations": {"vault-sync/source": m["vault"]}},
               "data": {k8s_key: base64.b64encode(str(data[vkey]).encode()).decode()
                        for k8s_key, vkey in m["keys"].items()}}
        url = f"{api}/api/v1/namespaces/{ns}/secrets/{m['secret']}"
        try:
            cur = req(url, headers=kh, ctx=kctx)
            if cur.get("data") == sec["data"]:
                print(f"unchanged {m['secret']}")
                continue
            req(url, "PUT", sec, kh, kctx)
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            req(f"{api}/api/v1/namespaces/{ns}/secrets", "POST", sec, kh, kctx)
        changed += 1
        print(f"synced   {m['vault']} → secret/{m['secret']} ({', '.join(m['keys'])})")
    print(f"{changed} secret(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
