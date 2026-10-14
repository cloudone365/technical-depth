# Chapter 12 · Open WebUI & LiteLLM

> **04-Qwen · Part IV — Applications · Chapter 12 of 14** · ← [Chapter 11 · RAG with Qwen, BGE & Qdrant](11-rag-with-qwen-bge-and-qdrant.md) · [All chapters](00-qwen-step-by-step-guide.md) · [Chapter 13 · Telemetry & troubleshooting](13-telemetry-and-troubleshooting.md) →

| | |
|---|---|
| **You will build** | Qwen models behind the lab's existing gateway: LiteLLM aliases for the Qwen catalog (`qwen`, `qwen-nothink`, `qwen-fast`, `agent`, `qwen-coder`, `qwen-vl`), a scoped virtual key with token and request limits you can watch trip, thinking on/off per alias and per Open WebUI preset, fallbacks between Qwen models, and Open WebUI showing Qwen3's thinking, calling tools natively and reading images with a Qwen-VL model — all on 03-DeepSeek's LiteLLM, PostgreSQL and Open WebUI, not a second copy |
| **Hardware** | dgx-spark-1 |
| **Time** | 75 min |
| **Risk** | Low. Editing the LiteLLM model list restarts LiteLLM (a few seconds of 503s on `api.lab.local`) |
| **Clusters** | `spark-root` only: `llm-serving` (serving-budget), behind Traefik in `ingress` at 192.168.0.115 |
| **Lab files** | reused from 03: [`k8s/apps/litellm.yaml`](../03-DeepSeek/lab/k8s/apps/litellm.yaml), [`k8s/apps/litellm-db.yaml`](../03-DeepSeek/lab/k8s/apps/litellm-db.yaml), [`k8s/apps/open-webui.yaml`](../03-DeepSeek/lab/k8s/apps/open-webui.yaml), [`k8s/apps/bge-m3.yaml`](../03-DeepSeek/lab/k8s/apps/bge-m3.yaml), [`tools/stream_probe.py`](../03-DeepSeek/lab/tools/stream_probe.py); from this module: [`models.yaml`](lab/models.yaml), [`scripts/serve-model.sh`](lab/scripts/serve-model.sh), [`tools/fc_check.py`](lab/tools/fc_check.py) |

Prerequisites: 03-DeepSeek [Chapter 12](../03-DeepSeek/12-open-webui-and-litellm-gateway.md) Tasks 5.1–5.2 (apps deployed, `api.lab.local` and `webui.lab.local` in the MacBook's `/etc/hosts`, the master key readable) and a Qwen model serving ([Chapter 04](04-serving-qwen-with-vllm-and-sglang.md)). That chapter covers the gateway itself — PostgreSQL, teams, spend, backups and the restore drill; this one covers what changes when the models behind it are Qwen.

---

## 1. Why this matters on a Spark

The Spark serves one or two chat models at a time and the set changes: a Qwen3 hybrid model for general chat, a Qwen2.5-Coder for the IDE plug-in, a Qwen-VL for a document session, an R1 distill from module 03 for a comparison. Clients should never know which one is loaded. LiteLLM gives them stable names; you move the names.

Qwen adds two gateway questions that a single-mode model doesn't raise:

1. **Thinking or not.** The same Qwen3 hybrid weights answer in two modes. Users want the fast mode for most chat and the thinking mode for hard questions; that choice must be reachable from Open WebUI and from SDK clients without a second copy of the model in memory.
2. **Tools and images.** Tool calling only works end to end if the gateway forwards `tools` untouched and the served model has the right parser (Chapter 10); images only work if the alias points at a VL model.

---

## 2. Architecture — HLD

```mermaid
flowchart LR
  subgraph CL["clients"]
    BR["browser<br/>webui.lab.local"]
    SDK["SDKs · agents · IDE<br/>api.lab.local"]
  end
  subgraph ROOT["spark-root"]
    TR["Traefik · ns ingress<br/>192.168.0.115 · timeouts 900 s"]
    subgraph LLMS["ns llm-serving · serving-budget"]
      UI["Open WebUI (03 apps)<br/>calls litellm:4000 in-cluster"]
      LL["LiteLLM (03 apps)<br/>qwen · qwen-fast · qwen-coder · agent · embeddings<br/>keys · tpm/rpm · fallbacks"]
      PG[("litellm-db<br/>keys · spend")]
      V["vllm · Qwen catalog model<br/>tool + reasoning parsers"]
      V2["second engine (optional)<br/>e.g. Qwen-VL or Coder"]
      E["bge-m3"]
    end
  end
  BR --> TR
  SDK --> TR
  TR --> LL
  TR --> UI
  UI --> LL
  LL --> PG
  LL --> V & V2 & E
  classDef gpu fill:#76b900,stroke:#3d6000,color:#000
  classDef net fill:#8250df,stroke:#4c2889,color:#fff
  classDef store fill:#bf8700,stroke:#7a5600,color:#fff
  classDef host fill:#0e7c86,stroke:#064e54,color:#fff
  class V,V2,E gpu
  class TR,LL net
  class PG store
  class UI,BR,SDK host
```

The data path is 03's: Traefik → LiteLLM → engine Service for API clients, and Traefik → Open WebUI → LiteLLM (in-cluster, `http://litellm.llm-serving:4000/v1`) for the browser. What this chapter changes is the **model list** in the `litellm-config` ConfigMap and the **model presets** in Open WebUI. LiteLLM addresses a vLLM engine by its Service and served model name (`hosted_vllm/<catalog name>`); the 04 overlays serve the catalog name, so an alias line names a catalog entry.

### 2.1 One request through the gateway, step by step

```mermaid
sequenceDiagram
  autonumber
  participant C as curl / SDK<br/>(MacBook)
  participant TR as Traefik<br/>192.168.0.115
  participant LL as LiteLLM<br/>litellm:4000
  participant PG as litellm-db
  participant V as vllm<br/>qwen3-8b
  participant V2 as vllm-qwen3-4b-instruct
  C->>TR: POST /v1/chat/completions<br/>model qwen · Bearer key · Host api.lab.local
  TR->>LL: HTTPRoute litellm (timeout 900 s)
  LL->>PG: look up the key: models · tpm_limit · rpm_limit
  alt key bucket empty
    LL-->>C: 429
  else main engine up
    LL->>V: hosted_vllm/qwen3-8b at vllm.llm-serving:8000/v1
    V-->>LL: content + reasoning
  else deploy/vllm parked
    LL->>V: retries fail (num_retries 2)
    LL->>V2: fallback qwen → qwen-fast
    V2-->>LL: content
  end
  LL->>PG: spend for the key
  LL-->>C: 200 + x-litellm-* headers
```

- **Steps 1–2** need `api.lab.local` → 192.168.0.115 in the MacBook's `/etc/hosts`. Open WebUI skips them: it calls `litellm:4000` inside the cluster.
- **Steps 3–4** are §5.4: the scoped key `qwen-tpm-test` gets `429` on the thinking alias first.
- **Steps 5–6** are §5.3: `thinking_chars` comes from the reasoning field; `qwen-nothink` adds `extra_body` to this hop.
- **Steps 7–9** are §5.5: the `x-litellm-attempted-fallbacks` header, and `x-litellm-model-api-base` pointing at `vllm-qwen3-4b-instruct`.
- **Step 10** is the `spend` that `/key/info` shows in §5.4.

---

## 3. LLD

### 3.1 Qwen aliases

| Alias | Points at (example catalog names) | Thinking | Clients |
|---|---|---|---|
| `qwen` | `qwen3-8b` (main engine, svc `vllm`) | on (model default) | Open WebUI "Qwen (thinking)", SDKs |
| `qwen-nothink` | the same engine, `enable_thinking: false` | off | test of the request-level switch (§5.3) |
| `qwen-fast` | `qwen3-4b-instruct` (side engine, svc `vllm-qwen3-4b-instruct`) | never | Open WebUI default, most SDK traffic |
| `agent` | `qwen3-4b-instruct` (same side engine) | never | agents with virtual keys (Chapter 10) |
| `qwen-coder` | `qwen3-coder-30b-fp8` (set `coding`, served as the main model) | — | IDE plug-ins, coding agents |
| `qwen-vl` | `qwen3-vl-8b` (side engine, when served) | — | image and document chats ([Chapter 03](03-vision-language-models.md)) |
| `embeddings` | `bge-m3` (unchanged from 03) | — | Open WebUI RAG, `rag_demo.py` |

The catalog set `everyday` is exactly this: `qwen3-8b` (util 0.20, 32 Gi) plus `qwen3-4b-instruct` (0.12, 20 Gi) next to the 03 apps (bge-m3 0.06) — Σ util 0.38, well inside 0.72. There are two ways to get a non-thinking answer, in order of preference:

1. **A non-thinking model** (`qwen-fast` → `qwen3-4b-instruct`). The *-Instruct-2507 models never think, so there is no request-level switch to get wrong. It costs a second engine (~15 GiB of the pool).
2. **The template switch on the shared engine** (`qwen-nothink`). Put `chat_template_kwargs: {enable_thinking: false}` into the request. From LiteLLM that means passing it through `litellm_params` as an extra body field, which depends on the LiteLLM version and the `drop_params` setting — §5.3 verifies it rather than trusting it. For Open WebUI, a model preset with the system prompt `/no_think` works on original Qwen3 hybrid models without any gateway support (not on the 2507 split models, which ignore soft switches).

### 3.2 Limits: what a token bucket does to a chat

LiteLLM enforces `tpm_limit` and `rpm_limit` per key (and per team, per model deployment). The behaviour is a token bucket: capacity $C$, refill rate $r = \text{TPM}/60$ per second, balance $B$. A request needing $k$ tokens at time $t$:

$$B(t) = \min\big(C,\; B(t_{\text{last}}) + r\,(t - t_{\text{last}})\big), \qquad \text{admit if } B(t) \ge k \text{, then } B \leftarrow B - k \text{; else HTTP 429}$$

For Qwen this matters because thinking multiplies the output tokens: a thinking answer can use 5–20× the tokens of the same answer without thinking. A key with `tpm_limit: 20000` that serves ~40 non-thinking answers a minute may serve only a handful with thinking on. Size TPM on the alias the key may use, and give thinking aliases to fewer keys. Record your own ratio in §5.4 rather than relying on the 5–20× range.

### 3.3 Failures, retries and cooldown

| LiteLLM setting (03 config) | Effect | Qwen use |
|---|---|---|
| `num_retries: 2` | retry the same deployment on 5xx/timeouts | covers a vLLM restart during a model switch |
| `allowed_fails: 3`, `cooldown_time: 60` | a deployment failing 3× in a minute is skipped for 60 s (a circuit breaker) | a crashed engine stops eating retries |
| `fallbacks` | on failure, try the next alias | `qwen` → `qwen-fast` → `reasoning-fast` |
| `timeout: 900` | per-request ceiling | thinking answers are long; matches Traefik's 900 s |

Retry back-off in most clients follows truncated exponential back-off with jitter, $t_{\text{wait}} = \min(t_{\max}, t_0 2^{n}) + U(0, \delta)$; with LiteLLM in front, client retries stack on top of gateway retries, so keep client retries low (1–2).

### 3.4 Open WebUI with Qwen

| Feature | What Qwen needs | Setting |
|---|---|---|
| Thinking display | vLLM `--reasoning-parser qwen3` so thinking arrives in its own field (`reasoning`, or `reasoning_content` in older releases) | nothing in Open WebUI; it renders a collapsible "Thought for N seconds" block |
| Thinking off for a preset | `/no_think` system prompt (hybrid models), or the `qwen-fast` alias | Workspace → Models → new model on top of `qwen`, system prompt |
| Native tool calling | the served model's tool-call parser (Chapter 10) | model's Advanced Params → Function Calling: Native |
| Images | alias pointing at a Qwen-VL model | the model must be marked with vision capability in Workspace → Models |
| Document RAG | `embeddings` alias (bge-m3) | unchanged from 03 |
| Long answers | Traefik and `AIOHTTP_CLIENT_TIMEOUT` at 900 s | set by 03's manifests |

---

## 4. Integrations

| With | How | Chapter |
|---|---|---|
| The gateway itself (DB, teams, spend, backup) | 03's apps and tasks | [03 Chapter 12](../03-DeepSeek/12-open-webui-and-litellm-gateway.md) |
| Serving the models | `scripts/serve-model.sh`; util sum ≲ 0.72 for two engines | [04](04-serving-qwen-with-vllm-and-sglang.md), [06](06-kubernetes-deployment-and-weight-caching.md) |
| Tool calling through the gateway | `fc_check.py run --model agent` | [10](10-qwen-agent-and-function-calling.md) |
| RAG | `embeddings` alias; `rag_demo.py` | [11](11-rag-with-qwen-bge-and-qdrant.md) |
| Secrets | master key and Open WebUI secret from Vault (`kv/spark-lab/…`) | [03 Chapter 15](../03-DeepSeek/15-automation-secrets-and-day2.md) |
| Streaming hops | Traefik timeouts, buffering | [03 Chapter 08](../03-DeepSeek/08-kubernetes-deployment-storage-and-streaming.md) |
| Metrics | LiteLLM and vLLM in Prometheus | [13](13-telemetry-and-troubleshooting.md) |

---

## 5. Lab

### 5.1 Serve the Qwen models and check the budget

#### What this lab does (`serve-model.sh`, set `everyday`)

- **Serves** `qwen3-8b` as the main engine (`svc/vllm`, util 0.20, 32 Gi) and `qwen3-4b-instruct` as a side engine (`svc/vllm-qwen3-4b-instruct`, util 0.12, 20 Gi) next to 03's apps (BGE-M3 at 0.06).
- **Reads** the util flag of each engine and the `serving-budget` quota. Nothing else changes.

#### What to look for

1. **Both runs:** `[PASS] … ready …`, `[PASS] answer: 144 …`, `0 failed`. The side run's budget line reads `running 0.26 + qwen3-4b-instruct 0.12 ≤ 0.72` with BGE-M3 up.
2. **Util per engine:**

   | Deployment | Healthy value |
   |---|---|
   | `vllm` | `gpu-memory-utilization=0.2` |
   | `vllm-qwen3-4b-instruct` | `gpu-memory-utilization=0.12` |
   | `bge-m3` | `gpu-memory-utilization=0.06` |

3. **Quota:** `limits.memory` used about 71Gi of 90Gi (32 + 20 for the engines, ~19 for the apps); `requests.memory` a little lower, because the app pods request less than their limits.

**If something's off:** no side pod and a `FailedCreate … exceeded quota` event → another engine (for example `qwen3-vl-8b`) still runs; stop it. More in §7.

#### Run it

```bash
# ▶ MacBook · 04-Qwen/lab
scripts/serve-model.sh list                              # the sets: everyday = qwen3-8b + qwen3-4b-instruct
scripts/serve-model.sh qwen3-8b
scripts/serve-model.sh qwen3-4b-instruct --side
for d in vllm vllm-qwen3-4b-instruct bge-m3; do
  echo "$d $(kubectl --context spark-root -n llm-serving get deploy $d -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -o 'gpu-memory-utilization=[0-9.]*')"
done
kubectl --context spark-root -n llm-serving describe quota serving-budget | grep -E 'memory|nvidia'
```

`serve-model.sh` refuses a model that would push Σ util over 0.72 (`FORCE=1` overrides it — don't). Memory is the tighter limit with the apps: 32 + 20 Gi for the two engines plus ~19 Gi for the 03 apps leaves no room in `serving-budget` (90 Gi) for a third 32 Gi engine such as `qwen3-vl-8b`; stop the side model (`scripts/serve-model.sh --stop-side qwen3-4b-instruct`) before serving the VL model as a side engine.

### 5.2 Add the Qwen aliases

#### What this lab does (ConfigMap `litellm-config` in `03-DeepSeek/lab/k8s/apps/litellm.yaml`)

- **You edit** the `model_list` in your working copy: six Qwen aliases (`qwen`, `qwen-nothink`, `qwen-fast`, `agent`, `qwen-coder`, `qwen-vl`) and two fallbacks. 03's other entries stay.
- **Applies** 03's app stack (only the ConfigMap changes if the apps already run) and restarts `deploy/litellm` so it reads the new list.
- **Reads** the LiteLLM master key from Secret `litellm-master-key` into a shell variable and lists the models at `http://api.lab.local`.

#### What to look for

1. **Apply:** `configmap/litellm-config configured`, the other objects `unchanged`.
2. **Rollout:** `deployment "litellm" successfully rolled out`.
3. **`/v1/models`:** `qwen`, `qwen-nothink`, `qwen-fast`, `agent`, `qwen-coder`, `qwen-vl`. Listed is not the same as served: only aliases whose engine runs will answer.

**If something's off:** `Could not resolve host: api.lab.local` → add `192.168.0.115 api.lab.local` to `/etc/hosts`. `401` → `KEY` is empty; check the Secret name. Aliases missing → LiteLLM still runs the old ConfigMap; restart it again.

#### Run it

Edit the `model_list` in `03-DeepSeek/lab/k8s/apps/litellm.yaml` (ConfigMap `litellm-config`). Add, keeping 03's entries (replace 03's `agent` entry):

```yaml
      - model_name: qwen
        litellm_params: {model: hosted_vllm/qwen3-8b, api_base: http://vllm.llm-serving:8000/v1, api_key: none}
      - model_name: qwen-nothink
        litellm_params:
          model: hosted_vllm/qwen3-8b
          api_base: http://vllm.llm-serving:8000/v1
          api_key: none
          extra_body: {chat_template_kwargs: {enable_thinking: false}}
      - model_name: qwen-fast
        litellm_params: {model: hosted_vllm/qwen3-4b-instruct, api_base: http://vllm-qwen3-4b-instruct.llm-serving:8000/v1, api_key: none}
      - model_name: agent
        litellm_params: {model: hosted_vllm/qwen3-4b-instruct, api_base: http://vllm-qwen3-4b-instruct.llm-serving:8000/v1, api_key: none}
      - model_name: qwen-coder
        litellm_params: {model: hosted_vllm/qwen3-coder-30b-fp8, api_base: http://vllm.llm-serving:8000/v1, api_key: none}
      - model_name: qwen-vl
        litellm_params: {model: hosted_vllm/qwen3-vl-8b, api_base: http://vllm-qwen3-vl-8b.llm-serving:8000/v1, api_key: none}
```

and extend `router_settings.fallbacks` with `{"qwen": ["qwen-fast"]}` and `{"qwen-fast": ["qwen"]}`. Only aliases whose catalog name is currently served will answer; the others fail and fall back where a fallback exists (§5.5).

```bash
# ▶ MacBook · 04-Qwen/lab
kubectl --context spark-root apply -k ../../03-DeepSeek/lab/k8s/apps
kubectl --context spark-root -n llm-serving rollout restart deploy/litellm
kubectl --context spark-root -n llm-serving rollout status deploy/litellm
KEY=$(kubectl --context spark-root -n llm-serving get secret litellm-master-key -o jsonpath='{.data.key}' | base64 -d)
API=http://api.lab.local
H=(-H "Authorization: Bearer $KEY" -H 'Content-Type: application/json')
curl -s $API/v1/models "${H[@]}" | jq -r '.data[].id' | grep -E '^(qwen|agent)'
```

Keep this edit in your fork of the repo (or in Git behind Argo CD, 03 Chapter 15); a later `apply -k` of an unedited checkout removes the aliases.

### 5.3 Verify the thinking switch through the gateway

#### What this lab does (three requests through LiteLLM)

- **Asks** the same question through `qwen`, `qwen-nothink` and `qwen-fast` and prints thinking characters, completion tokens and the answer for each. Read-only.
- **Tests** whether your LiteLLM version forwards `extra_body` (the `enable_thinking: false` switch) to vLLM.

#### What to look for

| Alias | Healthy value |
|---|---|
| `qwen` | `thinking_chars` above 0, many completion tokens, answer "No" (221 = 13 × 17) |
| `qwen-fast` | `thinking_chars` 0, few completion tokens |
| `qwen-nothink` | 0 if the gateway forwards `extra_body`; above 0 if it drops it. Record which |

**If something's off:** `thinking_chars` 0 on `qwen` too, with many completion tokens → the trace is in a field the `jq` doesn't read (it reads `reasoning` and `reasoning_content`); check the raw JSON. A 400 `model not found` → the served name differs from the alias target (§7).

#### Run it

```bash
# ▶ MacBook · 04-Qwen/lab
for m in qwen qwen-nothink qwen-fast; do
  curl -s $API/v1/chat/completions "${H[@]}" -d "{\"model\":\"$m\",\"max_tokens\":4096,
    \"messages\":[{\"role\":\"user\",\"content\":\"Is 221 prime? One line.\"}]}" \
  | jq -c "{alias: \"$m\", thinking_chars: (.choices[0].message.reasoning // .choices[0].message.reasoning_content // \"\" | length), completion_tokens: .usage.completion_tokens, answer: .choices[0].message.content}"
done
```

If `qwen-nothink` still thinks, the gateway dropped the field: rely on `qwen-fast`, on a `/no_think` preset in Open WebUI, or on per-request `chat_template_kwargs` from SDK clients, and remove the `qwen-nothink` alias so nobody depends on it. Record which methods work in your setup.

### 5.4 A scoped key and the thinking cost

#### What this lab does (LiteLLM `/key/generate`)

- **Creates** a virtual key `qwen-tpm-test` in LiteLLM's database: allowed models `qwen`, `qwen-nothink`, `qwen-fast`; 6,000 tokens and 30 requests per minute; valid for 1 day.
- **Sends** 10 requests through `qwen-fast`, waits 65 s for the bucket to refill, then 10 through `qwen`, and prints the HTTP codes.
- **Reads** the key's spend and limits.

#### What to look for

1. **`qwen-fast`:** mostly `200`.
2. **`qwen`:** `429` after a few `200`s; the thinking tokens empty the bucket.
3. **Key info:** `key_alias: "qwen-tpm-test"`, `tpm_limit: 6000`, `rpm_limit: 30`, a `spend` value.
4. **Record** requests per minute per alias; that ratio sizes TPM for thinking aliases.

**If something's off:** `QK` is `null` → the master key in `H` is wrong or the database isn't reachable (`kubectl --context spark-root -n llm-serving logs deploy/litellm`). No `429` at all → the limits aren't enforced in your LiteLLM version; check the key info.

#### Run it

```bash
# ▶ MacBook · 04-Qwen/lab
QK=$(curl -s $API/key/generate "${H[@]}" -d '{"key_alias":"qwen-tpm-test","models":["qwen","qwen-nothink","qwen-fast"],"tpm_limit":6000,"rpm_limit":30,"duration":"1d"}' | jq -r .key)
Q=(-H "Authorization: Bearer $QK" -H 'Content-Type: application/json')
for m in qwen-fast qwen; do
  codes=""
  for i in $(seq 10); do
    codes+=$(curl -s -o /dev/null -w '%{http_code} ' $API/v1/chat/completions "${Q[@]}" \
      -d "{\"model\":\"$m\",\"max_tokens\":2048,\"messages\":[{\"role\":\"user\",\"content\":\"Explain KV cache in two sentences.\"}]}")
  done
  echo "$m: $codes"; sleep 65
done
curl -s "$API/key/info?key=$QK" "${H[@]}" | jq '.info | {key_alias, spend, tpm_limit, rpm_limit}'
```

Same question, same limit: the thinking tokens use up the bucket (§3.2). `qwen-fast` is also a smaller model, so for a same-model comparison repeat the loop with `qwen-nothink` against `qwen` if §5.3 showed the switch works. Record how many requests each alias got through per minute; that ratio is your TPM sizing factor for thinking aliases.

### 5.5 Fallback between Qwen models

#### What this lab does (park the main engine, send two requests)

- **Takes `deploy/vllm` to 0** with the KEDA pause annotation, so `qwen` and `qwen-nothink` lose their engine. KEDA owns that Deployment; a plain `kubectl scale` is undone.
- **Sends** one short request through each alias and prints the status, the time and LiteLLM's `x-litellm-*` response headers.
- **Restores** the main engine afterwards.

#### What to look for

1. **`qwen`:** `200`, an `x-litellm-attempted-fallbacks` header, and an `x-litellm-model-api-base` pointing at `vllm-qwen3-4b-instruct`. The time is higher than usual (the failed attempts).
2. **`qwen-nothink`:** a 5xx after the retries; it has no fallback and shares the parked engine.
3. **Afterwards:** `deployment "vllm" successfully rolled out`.

**If something's off:** both aliases still answer from `vllm` → the pause annotation is missing (`kubectl --context spark-root -n llm-serving get scaledobject vllm -o yaml`). `qwen` fails too → the fallback entry is missing from `router_settings` (§5.2).

#### Run it

```bash
# ▶ MacBook · 04-Qwen/lab
kubectl --context spark-root -n llm-serving annotate scaledobject vllm autoscaling.keda.sh/paused-replicas=0 --overwrite
sleep 60                                           # KEDA scales to 0 within its polling interval; the pod drains (preStop: 5 s, then until idle)
for m in qwen qwen-nothink; do
  curl -s -D - -o /dev/null -w "$m %{http_code} %{time_total}s\n" $API/v1/chat/completions "${H[@]}" \
    -d "{\"model\":\"$m\",\"max_tokens\":64,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}" \
    | grep -iE '^x-litellm-(model-api-base|attempted-fallbacks)|^qwen'
done
kubectl --context spark-root -n llm-serving annotate scaledobject vllm autoscaling.keda.sh/paused-replicas-
kubectl --context spark-root -n llm-serving rollout status deploy/vllm --timeout=20m
```

Note the extra latency the failed attempts added. Aliases on the same engine fail together; only a fallback to a *different* engine helps. The same mechanism reaches across model families: `{"qwen": ["qwen-fast", "reasoning-fast"]}` with an R1 distill from 03 served as an engine. Whether a cross-family fallback is acceptable is a product decision: the answer style changes.

### 5.6 Open WebUI presets for Qwen

#### What this lab does (Open WebUI presets + `03-DeepSeek/lab/tools/stream_probe.py`)

- **In the UI** at `http://webui.lab.local`: you create presets "Qwen (thinking)", "Qwen (fast)", an agent preset with native function calling, and optionally a vision preset. These live in Open WebUI's database; no manifest changes.
- **Vision preset:** swaps the side engine from `qwen3-4b-instruct` to `qwen3-vl-8b` (memory, §5.1) and back afterwards.
- **Timing:** `stream_probe.py` streams one answer through the gateway per alias and times each phase.

#### What to look for

1. **The checkpoint question:** the thinking preset shows a collapsible thinking block, then **14**; the fast preset answers 14 directly.
2. **Agent preset:** a tool call appears in the chat.
3. **`stream_probe.py`:**

   | Field | Healthy value |
   |---|---|
   | `first reasoning` | a number for `qwen`; `—` for `qwen-fast` |
   | `first answer` | much later for `qwen` than for `qwen-fast` |
   | `finish_reason` | `stop` |
   | `BUFFERED` line | absent |

**If something's off:** `<think>` shown as text in the UI → the engine lacks `--reasoning-parser`. `BUFFERED` → a hop buffers SSE (03 Chapter 08). `TRUNCATED` → raise max tokens. More in §7.

#### Run it

In `http://webui.lab.local` as admin:

1. **Workspace → Models → +**: base model `qwen`, name "Qwen (thinking)". Save.
2. Again: base model `qwen-fast`, name "Qwen (fast)" — or, without the side engine, base model `qwen` with the system prompt `/no_think` (works on the hybrid `qwen3-8b`). Make it the default model (**Admin Panel → Settings → Models**).
3. For the agent preset: base `agent`, **Advanced Params → Function Calling: Native**, attach a tool (Open WebUI **Workspace → Tools**). Ask something that needs the tool and check the call appears in the chat.
4. For images: `scripts/serve-model.sh --stop-side qwen3-4b-instruct` then `scripts/serve-model.sh qwen3-vl-8b --side` (§5.1 explains why one has to go). Base `qwen-vl`, enable the **Vision** capability, then paste a screenshot of a Grafana panel and ask what it shows. Swap the side engines back afterwards.

Ask both Qwen presets *"A job checkpoints every 7 steps; how many checkpoints after 100 steps?"*. Time both through the gateway hop by hop:

```bash
# ▶ MacBook · 04-Qwen/lab
python3 ../../03-DeepSeek/lab/tools/stream_probe.py --url http://api.lab.local --api-key "$KEY" --model qwen
python3 ../../03-DeepSeek/lab/tools/stream_probe.py --url http://api.lab.local --api-key "$KEY" --model qwen-fast
```

Compare *time to first answer token*: with thinking it is the length of the thinking phase, which is what users perceive as "slow".

### 5.7 Tools through the gateway

#### What this lab does (`tools/fc_check.py` through LiteLLM)

- **Runs** the six-case function-calling suite against alias `agent` at `http://api.lab.local` with the master key. Read-only.

#### What to look for

1. **Summary line:** the same `fc_check: N/6 cases passed` and valid-JSON ratio as the direct `qwen3-4b-instruct` run in Chapter 10 §5.3.

**If something's off:** fewer passes than direct, especially `parallel` or `no-tool` → the gateway dropped or rewrote `tools` / `tool_choice` (`drop_params: true` in 03's config). More in §7.

#### Run it

```bash
# ▶ MacBook · 04-Qwen/lab
python3 tools/fc_check.py run --url http://api.lab.local --api-key "$KEY" --model agent
```

The pass rate must match the direct `qwen3-4b-instruct` run of Chapter 10 §5.3. A difference means the gateway changed the request (dropped `tools`, `tool_choice` or parallel calls).

---

## 6. Verify

| Check | Command | Expected |
|---|---|---|
| Aliases exist | §5.2 last command | `qwen`, `qwen-nothink`, `qwen-fast`, `agent`, `qwen-coder`, `qwen-vl` |
| Thinking switch | §5.3 | `qwen` thinks; `qwen-fast` doesn't; `qwen-nothink` result recorded |
| Limits enforced | §5.4 | `429` once the bucket is empty; thinking alias trips first |
| Fallback | §5.5 | `qwen` answered by the side engine; `qwen-nothink` fails with its engine |
| Open WebUI | §5.6 | thinking block on the thinking preset only; native tool call visible |
| Tool parity | §5.7 | same `fc_check.py` pass rate as direct |
| Budget | `kubectl --context spark-root -n llm-serving describe quota serving-budget` | used ≤ hard |

---

## 7. Troubleshooting

| You see | Cause | Fix |
|---|---|---|
| `qwen` returns 400 "model not found" | catalog name in `hosted_vllm/<name>` differs from the served name | `kubectl --context spark-root -n llm-serving get deploy vllm -o jsonpath='{.metadata.labels.model}'`; aliases must use that name |
| New aliases missing after a deploy | an unedited `apply -k` reset the ConfigMap | keep the edit in Git; `rollout restart deploy/litellm` after changing it |
| `qwen-nothink` still thinks | `extra_body` not forwarded by this LiteLLM version, or `drop_params` removed it | use `qwen-fast` (non-thinking model), a `/no_think` preset, or client-side `chat_template_kwargs` |
| Open WebUI shows `<think>` as text | engine without `--reasoning-parser qwen3` | catalog entry for the model; re-run `serve-model.sh` |
| Thinking block never closes, answer empty | `max_tokens` used up in thinking | raise the model's max tokens in the preset; use the fast preset |
| `429` for a user who sent few requests | TPM bucket emptied by thinking tokens | larger `tpm_limit` for thinking aliases, or give the key `qwen-fast` only |
| Tool calls work direct, not through the gateway | `tools` dropped or rewritten | `hosted_vllm/` provider; check `drop_params`; `fc_check.py` direct vs gateway |
| Images ignored in Open WebUI | alias is a text model, or vision capability off in the preset | point `qwen-vl` at a VL catalog entry; enable Vision |
| Streams stall at ~60 s | a hop with a default timeout | 03 Chapter 08: Traefik HTTPRoute timeout, `AIOHTTP_CLIENT_TIMEOUT`, LiteLLM `timeout` |
| `serve-model.sh` refuses with "budget", or a side engine is Pending | Σ util > 0.72, or `serving-budget` memory full | `--stop-side` a model first; Chapter 06 budget table and the catalog `sets` |

---

## 8. Scale-out path

With dgx-spark-2, give each alias its own engine: `qwen` (thinking) on one Spark, `qwen-fast` (a non-thinking model) and `qwen-coder` on the other, so the thinking traffic can't starve the fast path. For one alias served on both Sparks, list two deployments under the same `model_name` with `routing_strategy: least-busy` (03 Chapter 12 Task 5.9); prefix caching works per engine, so for long multi-turn chats prefer sticky routing by user. LiteLLM itself is stateless apart from PostgreSQL and scales with replicas.

---

## 9. Checklist

- [ ] The Qwen aliases are in `litellm-config` (in Git), and each points at a served catalog name.
- [ ] I verified which non-thinking methods work in my versions (`qwen-fast`, `qwen-nothink`, `/no_think`) and documented them.
- [ ] A scoped key with `tpm_limit` tripped on the thinking alias first; I recorded the ratio.
- [ ] Fallback behaviour is known: aliases on one engine fail together; `qwen` falls back to the side engine; cross-family fallback is a deliberate choice.
- [ ] Open WebUI has a thinking preset, a fast default preset and an agent preset with native function calling.
- [ ] `fc_check.py` gives the same result through the gateway as direct.
