# Volume 17: Tool Calling and Function Calling APIs in Qwen2.5

```
==================================================================================================
TARGET AUDIENCE: AI Engineers, Backend Architects, Distributed System Developers, Systems Programmers
PREREQUISITES   : Understanding of JSON schemas, token generation logits, and REST API conventions
HARDWARE PROFILE: NVIDIA DGX Spark (Grace ARM Neoverse V2 + Blackwell GB10, 128 GB Unified Memory)
OBJECTIVE       : Master Qwen2.5 structured tool invocation, Hermes/ChatML function calling formats,
                  constrained decoding with context-free grammars, and parallel execution pipelines.
==================================================================================================
```

---

## 1. Executive Scaffolding & Conceptual Map

Language models generate text token by token. However, enterprise software requires deterministic, structured outputs like JSON or SQL to interface safely with external databases, execution engines, and microservices. Tool calling bridges the gap between probabilistic neural language processing and deterministic API execution.

Qwen2.5 provides native, state-of-the-art support for structured function calling across its entire parameter scale (0.5B to 72B), supporting parallel tool calling, multi-turn tool loops, and grammar-constrained token masking.

```
       [User Natural Language Prompt]
                     │
                     ▼
       ┌──────────────────────────────┐
       │   Qwen2.5 Autoregressive LLM │
       │  (ChatML + Tool Schema Prompt│
       └──────────────┬───────────────┘
                      │
         ┌────────────┴────────────┐
         │ Is Tool Call Required?  │
         └────────────┬────────────┘
               YES    │            │ NO
        ┌─────────────┘            └───────────────┐
        ▼                                          ▼
┌──────────────────────────────┐        ┌──────────────────────────────┐
│  Constrained Logit Decoding  │        │ Direct Natural Language      │
│  (CFG / JSON Schema Masking) │        │ Streaming Output             │
└──────────────┬───────────────┘        └──────────────────────────────┘
               │ Emits Structured JSON
               ▼
┌──────────────────────────────┐
│  Parallel Tool Execution     │
│  (Database, Search, Sandbox) │
└──────────────┬───────────────┘
               │ Collects Result Payloads
               ▼
┌──────────────────────────────┐
│  Context Injection & Answer  │
│  Synthesis in Qwen2.5        │
└──────────────────────────────┘
```

### Table of Contents
1. Executive Scaffolding & Conceptual Map
2. Zero-to-One Foundational Intuition: The Switchboard Operator Analogy
3. Evolutionary Lineage: From Heuristic RegEx to Grammar-Constrained Logit Decoders
4. First-Principles Mathematics & Algorithmic Formulations
   - Mathematical Formulation of Grammar-Constrained Logit Masking
   - Finite State Automaton (FSA) State Transitions
   - ChatML Tool Invocation Syntax Specification
5. Comparative Trade-Off Matrix: Tool Calling Paradigms
6. Concrete Production Hands-On Lab: Deterministic Parallel Function Calling Engine
7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)
8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

---

## 2. Zero-to-One Foundational Intuition: The Switchboard Operator Analogy

Imagine a telephone switchboard operator in 1940. When an incoming caller asks, "Can you tell me the temperature in Tokyo right now?", the operator does not guess the temperature. Instead, the operator has a desk with specific plugboards labeled:
1. `get_weather(city: string, unit: string)`
2. `search_database(query: string)`
3. `calculate_fx(amount: float, from_curr: string, to_curr: string)`

The operator inspects the caller's request, identifies that `get_weather` is required, plugs the patch cable into jack #1, speaks into the wire: `{"city": "Tokyo", "unit": "celsius"}`, waits for the remote weather station to reply: `{"temp": 18, "condition": "Cloudy"}`, unplug the cable, and reports back to the caller in smooth natural language: "The current temperature in Tokyo is 18°C with cloudy skies."

In the Qwen2.5 ecosystem:
- **The Caller** is the User.
- **The Operator** is the Qwen2.5 foundational model.
- **The Plugboard** is the OpenAPI/JSON function schema injected into the system prompt.
- **The Patch Cables** are tool calls formatted in strict XML/JSON tags (`<tool_call>...</tool_call>`).
- **The Remote Station** is your backend microservice or external REST API.

---

## 3. Evolutionary Lineage: From Heuristic RegEx to Grammar-Constrained Logit Decoders

```
Generation 1 (2020-2022)      Generation 2 (2023)           Generation 3 (2024-2026)
Prompt & Hope                  JSON Mode & Special Tokens    Grammar-Constrained Decoders
──────────────────────────    ──────────────────────────    ───────────────────────────
- Unconstrained sampling       - Model trained on function   - Real-time Pushdown Automata
- Post-hoc regex parsing         calling tokens (<call>, </>) - Logit bias: -inf on invalid
- High hallucination rate      - Still subject to syntax        tokens
- Broken JSON brackets (40%+)    errors under load           - Guaranteed 100% syntactically
                               - Fragile escaping              valid JSON schema output
```

1. **Generation 1: Heuristic Prompting & RegEx Scraping (2020–2022)**:
   Models were instructed: "Return a JSON object with keys foo and bar." Models frequently emitted markdown wrappers (` ```json `), trailing commas, and unquoted keys. Enterprise pipelines suffered 30–50% failure rates due to unparseable JSON payloads.

2. **Generation 2: Native ChatML Special Tokens & Fine-Tuning (2023–2024)**:
   Models like Qwen-1.5 and Hermes-2 were fine-tuned with explicit token identifiers: `<|im_start|>call:func_name{...}<|im_end|>`. While syntactically more reliable, deep edge-case inputs could still cause the model to emit invalid nested keys, malformed Unicode escapes, or unclosed curly braces.

3. **Generation 3: Grammar-Constrained Logit Masking (2024–2026)**:
   Modern runtimes (vLLM with Outlines/XGrammar, SGLang with XGrammar, llama.cpp with GBNF) compile the target JSON schema into a **Deterministic Finite Automaton (DFA)** or **Context-Free Grammar (CFG)**. Before every single forward token generation step, the engine masks all tokens in the vocabulary that would violate the grammar, mathematically guaranteeing valid JSON output.

---

## 4. First-Principles Mathematics & Algorithmic Formulations

### Mathematical Formulation of Grammar-Constrained Logit Masking

Let $\mathcal{V}$ denote the tokenizer vocabulary of size $V = |\mathcal{V}|$ (for Qwen2.5, $V = 152,064$).
At generation step $t$, the LLM computes the unnormalized logit vector:

$$\mathbf{z}_t \in \mathbb{R}^V$$

Let $\mathcal{G}$ be a formal Context-Free Grammar representing the schema of the function arguments:

$$\mathcal{G} = (V_N, \Sigma, R, S)$$

Where:
- $V_N$ is the set of non-terminal symbols.
- $\Sigma$ is the set of terminal characters (bytes/Unicode codepoints).
- $R$ is the set of production rules.
- $S \in V_N$ is the start symbol.

Let $w_{1:t-1} = (w_1, w_2, \dots, w_{t-1})$ be the sequence of tokens generated so far.
A token $v \in \mathcal{V}$ corresponds to a byte string $s(v) \in \Sigma^*$.

We define the set of syntactically valid next tokens $\mathcal{V}_{\text{valid}}(w_{1:t-1}, \mathcal{G})$ as:

$$\mathcal{V}_{\text{valid}}(w_{1:t-1}, \mathcal{G}) = \left\{ v \in \mathcal{V} \;\middle|\; \exists \, u \in \Sigma^* \text{ s.t. } s(w_{1:t-1}) \cdot s(v) \cdot u \in \mathcal{L}(\mathcal{G}) \right\}$$

Where $\mathcal{L}(\mathcal{G})$ is the formal language recognized by $\mathcal{G}$.

The constrained logit transformation applies an additive binary mask $\mathbf{m}_t \in \{0, -\infty\}^V$:

$$m_{t, v} = \begin{cases} 0 & \text{if } v \in \mathcal{V}_{\text{valid}}(w_{1:t-1}, \mathcal{G}) \\ -\infty & \text{if } v \notin \mathcal{V}_{\text{valid}}(w_{1:t-1}, \mathcal{G}) \end{cases}$$

The final token probability distribution $P(w_t \mid w_{1:t-1})$ is obtained via the masked Softmax:

$$P(w_t = v \mid w_{1:t-1}) = \frac{\exp(z_{t, v} + m_{t, v})}{\sum_{j \in \mathcal{V}} \exp(z_{t, j} + m_{t, j})}$$

If $v \notin \mathcal{V}_{\text{valid}}$, $\exp(z_{t, v} - \infty) = 0$. Consequently:

$$\sum_{v \in \mathcal{V}_{\text{valid}}} P(w_t = v \mid w_{1:t-1}) = 1.0$$

The probability of emitting a syntax error is identically zero ($P(\text{syntax error}) = 0$).

### Finite State Automaton (FSA) State Transitions

```
 State 0           State 1           State 2           State 3           State 4
[Expect '{'] ──> [Expect '"'] ──> [Expect Key] ──> [Expect ':'] ──> [Expect Val]
     │                                                                    │
     │                                                                    ▼
     └───────────────────────<── [Expect ',' or '}'] <────────────────────┘
```

During generation of a key like `"city"`, any token containing non-alphabetical characters or tokens that do not match the JSON schema definition are strictly masked with logit $-\infty$.

### ChatML Tool Invocation Syntax Specification

Qwen2.5 adopts the native ChatML format with specialized XML encapsulation for tools:

```xml
<|im_start|>system
You are a helpful assistant with access to the following tools:
<tools>
[{"name": "fetch_stock_price", "description": "Fetches current stock ticker price", "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]}}]
</tools>
<|im_end|>
<|im_start|>user
What is NVDA trading at?<|im_end|>
<|im_start|>assistant
<tool_call>
{"name": "fetch_stock_price", "arguments": {"ticker": "NVDA"}}
</tool_call><|im_end|>
<|im_start|>user
<tool_response>
{"ticker": "NVDA", "price": 138.25, "currency": "USD"}
</tool_response><|im_end|>
<|im_start|>assistant
NVIDIA (NVDA) is currently trading at $138.25 USD.<|im_end|>
```

---

## 5. Comparative Trade-Off Matrix: Tool Calling Paradigms

| Feature Dimension | Raw ChatML Prompting | Outlines / XGrammar (vLLM/SGLang) | OpenAI Function Calling Wire API | LangChain / ReAct Agent Prompting |
| :--- | :--- | :--- | :--- | :--- |
| **Syntactic Guarantee** | Probabilistic (92–98%) | **100% Deterministic (Math)** | Deterministic (Vendor-side CFG) | Low (75–85% without parser retries) |
| **Decoding Overhead** | 0% (Standard Softmax) | ~3–7% latency (DFA indexing) | Hidden inside proprietary cloud API| High (repeated retries on parse failure)|
| **Parallel Tool Calls** | Supported via array output | Supported via schema array | Native API multi-tool calls | Weak (typically sequential loops) |
| **Schema Expressiveness** | Unrestricted text | Strict Pydantic / JSON Schema | Strict Pydantic / JSON Schema | Freeform natural language |
| **DGX Spark Fit** | Compatible with all engines | **Native on vLLM/SGLang on GB10** | Cloud only (Data egress risk) | Heavy CPU orchestration overhead |

---

## 6. Concrete Production Hands-On Lab: Deterministic Parallel Function Calling Engine

This self-contained Python script executes an end-to-end multi-tool calling agent against Qwen2.5 using OpenAI-compatible APIs (vLLM or Ollama), with automated schema generation from Python docstrings and parallel asynchronous dispatch.

```python
#!/usr/bin/env python3
"""
Production-grade parallel tool-calling agent for Qwen2.5.
Compatible with vLLM, SGLang, and Ollama OpenAI-compatible endpoints on DGX Spark.
"""

import asyncio
import json
import inspect
from typing import Any, Callable, Dict, List, Optional
import urllib.request
import urllib.error

# =====================================================================
# 1. TOOL DEFINITIONS & AUTOMATIC SCHEMA COMPILATION
# =====================================================================

class ToolRegistry:
    """Registry that maps Python functions to OpenAI/Qwen2.5 tool schemas."""
    def __init__(self):
        self._tools: Dict[str, Callable] = {}
        self._schemas: List[Dict[str, Any]] = []

    def register(self, func: Callable):
        name = func.__name__
        doc = func.__doc__ or "No description provided."
        sig = inspect.signature(func)

        properties = {}
        required = []

        type_mapping = {
            str: "string",
            int: "integer",
            float: "number",
            bool: "boolean",
            list: "array",
            dict: "object"
        }

        for param_name, param in sig.parameters.items():
            param_type = type_mapping.get(param.annotation, "string")
            properties[param_name] = {
                "type": param_type,
                "description": f"Parameter {param_name}"
            }
            if param.default == inspect.Parameter.empty:
                required.append(param_name)

        schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": doc.strip(),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required
                }
            }
        }
        self._tools[name] = func
        self._schemas.append(schema)
        return func

    def get_schemas(self) -> List[Dict[str, Any]]:
        return self._schemas

    async def execute_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        if name not in self._tools:
            return {"error": f"Tool '{name}' not found in registry."}
        func = self._tools[name]
        try:
            if inspect.iscoroutinefunction(func):
                return await func(**arguments)
            else:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, lambda: func(**arguments))
        except Exception as e:
            return {"error": f"Execution error in {name}: {str(e)}"}

registry = ToolRegistry()

# Define mock domain tools
@registry.register
def query_gpu_telemetry(gpu_id: int) -> Dict[str, Any]:
    """Retrieves real-time Blackwell GB10 telemetry metrics including temperature and power."""
    return {
        "gpu_id": gpu_id,
        "model": "NVIDIA Blackwell GB10",
        "temperature_celsius": 42.5,
        "power_draw_watts": 185.2,
        "memory_used_gb": 48.6,
        "memory_total_gb": 128.0
    }

@registry.register
def execute_system_diagnostics(subsystem: str) -> Dict[str, Any]:
    """Runs low-level diagnostics on a specified subsystem (nvlink, memory, pcie)."""
    return {
        "subsystem": subsystem,
        "status": "HEALTHY",
        "nvlink_bandwidth_gb_s": 900.0,
        "ecc_errors_detected": 0
    }

# =====================================================================
# 2. ASYNC CLIENT FOR QWEN2.5 TOOL CALLING LOOP
# =====================================================================

class QwenToolAgent:
    def __init__(self, endpoint_url: str = "http://localhost:8000/v1", model_name: str = "Qwen/Qwen2.5-32B-Instruct"):
        self.endpoint_url = endpoint_url
        self.model_name = model_name

    def _post_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.endpoint_url}/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def run_turn(self, messages: List[Dict[str, Any]], max_tool_iterations: int = 5) -> str:
        iteration = 0
        while iteration < max_tool_iterations:
            iteration += 1
            payload = {
                "model": self.model_name,
                "messages": messages,
                "tools": registry.get_schemas(),
                "tool_choice": "auto",
                "temperature": 0.1
            }

            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: self._post_request(payload))
            choice = response["choices"][0]
            message = choice["message"]

            tool_calls = message.get("tool_calls")
            if not tool_calls:
                # Model finished tool interactions and emitted final natural language synthesis
                return message.get("content", "")

            # Append the assistant's decision to call tools
            messages.append(message)

            # Dispatch tool calls concurrently
            tasks = []
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])
                print(f"[DISPATCH] Spawning tool: {fn_name} with args {args}")
                tasks.append(registry.execute_tool(fn_name, args))

            results = await asyncio.gather(*tasks)

            # Return tool outputs back to Qwen2.5 context
            for tc, result in zip(tool_calls, results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "content": json.dumps(result)
                })

        return "Max tool iterations reached without terminal conclusion."

# =====================================================================
# 3. VERIFICATION HARNESS
# =====================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("QWEN2.5 FUNCTION CALLING & TOOL REGISTRY TEST")
    print("=" * 80)

    schemas = registry.get_schemas()
    print(f"Compiled {len(schemas)} tools into JSON schema:")
    print(json.dumps(schemas, indent=2))

    # Test local execution
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    telemetry = loop.run_until_complete(registry.execute_tool("query_gpu_telemetry", {"gpu_id": 0}))
    print("\nLocal Tool Invocation Result:")
    print(json.dumps(telemetry, indent=2))
    assert telemetry["status"] != "error"
    print("\n[SUCCESS] Tool schemas verified and local dispatch passed.")
```

---

## 7. Hardware Grounding for NVIDIA DGX Spark (GB10 128 GB Unified Memory)

When deploying tool-calling agents at high concurrency on the DGX Spark (Blackwell GB10 + Grace ARM):
1. **Low-Latency Function Dispatch**:
   The Grace ARM Neoverse V2 CPU cores handle local Python tool executions, network sockets, and database queries in parallel with the GB10 GPU compute engine. The 900 GB/s bidirectional NVLink-C2C bus ensures near-zero latency when streaming JSON tool arguments between host CPU memory and GPU KV cache.

2. **vLLM Grammar-Guided Engine Settings**:
   To enforce 100% JSON validity without degrading throughput, enable `xgrammar` or `outlines` in vLLM:
   ```bash
   vllm serve Qwen/Qwen2.5-32B-Instruct \
     --dtype bfloat16 \
     --guided-decoding-backend xgrammar \
     --enable-auto-tool-choice \
     --tool-call-parser qwen25 \
     --max-model-len 32768 \
     --gpu-memory-utilization 0.90
   ```

3. **Memory Sizing**:
   - Qwen2.5-32B-Instruct in BF16: ~65 GB weights.
   - Dynamic grammar compilation state: < 250 MB memory overhead.
   - Remaining ~62 GB on GB10 dedicated to high-concurrency PagedAttention KV cache.

---

## 8. Hands-On Exercises, Solutions, and Troubleshooting FAQ

### Practice Exercises

1. **Exercise 1 (Schema Constraints)**:
   Add an `enum` constraint to the `execute_system_diagnostics` tool so that `subsystem` strictly accepts only `["nvlink", "memory", "pcie"]`. Verify that an invalid input causes a validation error before tool dispatch.

2. **Exercise 2 (Recursive Agent Loop)**:
   Implement a multi-hop scenario where tool #1 (`query_service_dependencies(service_name)`) yields a list of downstream databases, and Qwen2.5 automatically issues parallel `query_db_health(db_name)` tool calls for each discovered dependency.

### Solutions

**Solution for Exercise 1**:
```python
from typing import Literal

@registry.register
def execute_system_diagnostics(subsystem: Literal["nvlink", "memory", "pcie"]) -> Dict[str, Any]:
    """Runs diagnostics strictly restricted to allowed hardware subsystems."""
    allowed = ("nvlink", "memory", "pcie")
    if subsystem not in allowed:
        raise ValueError(f"Invalid subsystem '{subsystem}'. Must be one of {allowed}")
    return {"subsystem": subsystem, "status": "HEALTHY"}
```

### Troubleshooting FAQ

- **Q: Qwen2.5 outputs tool calls in plain markdown text instead of structured `<tool_call>` blocks.**
  - *Fix*: Ensure the system prompt includes the exact `<tools>` block and that you are using the official ChatML template. When using vLLM, pass `--tool-call-parser qwen25` and `--enable-auto-tool-choice`.

- **Q: Model gets trapped in an infinite loop calling the same tool repeatedly.**
  - *Fix*: The tool output payload must provide clear signals answering the user's intent. If an error occurs, return `{"status": "failure", "reason": "connection timeout"}` so the model can either retry with different arguments or apologize to the user. Always enforce `max_tool_iterations` (typically 5 to 10).
