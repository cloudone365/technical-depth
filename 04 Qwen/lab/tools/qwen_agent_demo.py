#!/usr/bin/env python3
"""A Qwen-Agent assistant on the Spark's own vLLM (Volume 16).

Qwen-Agent supplies the agent loop, function-calling prompt formats, memory and
built-in tools; we register two lab tools (read-only cluster query and a safe
calculator, both from 03's agent_tools.py) and optionally its code_interpreter
(executes Python — only inside the sandboxed pod of k8s/jobs/qwen-agent.yaml).

  python3 qwen_agent_demo.py "How many GPU slices are in use and what share of 4 is that?"
  python3 qwen_agent_demo.py --code "Plot nothing; just compute the mean of [3, 9, 12] with Python."
Env: OPENAI_BASE (default http://vllm.llm-serving:8000/v1), MODEL (default qwen2.5-7b).
Needs: pip install "qwen-agent[code_interpreter]==0.0.29".
"""
import argparse
import json
import os
import sys

for p in ("/app/ds-tools", os.path.join(os.path.dirname(__file__), "../../../03 DeepSeek/lab/tools")):
    sys.path.insert(0, os.path.abspath(p))
import agent_tools  # noqa: E402  (03 DeepSeek: calculator, gpu_slices with in-cluster RBAC)

from qwen_agent.agents import Assistant  # noqa: E402
from qwen_agent.tools.base import BaseTool, register_tool  # noqa: E402


@register_tool("gpu_slices")
class GpuSlices(BaseTool):
    description = "Count GPU time-slices requested by running pods, per namespace (read-only)."
    parameters = {"type": "object", "properties": {}, "required": []}

    def call(self, params, **kwargs):
        return json.dumps(agent_tools.gpu_slices())


@register_tool("calculator")
class Calculator(BaseTool):
    description = "Evaluate an arithmetic expression such as 3 / 4 * 100."
    parameters = {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}

    def call(self, params, **kwargs):
        args = json.loads(params) if isinstance(params, str) else params
        try:
            return json.dumps(agent_tools.calculator(args["expression"]))
        except (ValueError, KeyError, SyntaxError) as e:
            return json.dumps({"error": str(e)})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--code", action="store_true", help="also enable Qwen-Agent's code_interpreter (sandbox only)")
    a = ap.parse_args()
    llm = {"model": os.environ.get("MODEL", "qwen2.5-7b"),
           "model_server": os.environ.get("OPENAI_BASE", "http://vllm.llm-serving:8000/v1"),
           "api_key": os.environ.get("OPENAI_API_KEY", "EMPTY"),
           "generate_cfg": {"temperature": 0.2, "fncall_prompt_type": "nous"}}   # Hermes-style <tool_call> format
    tools = ["gpu_slices", "calculator"] + (["code_interpreter"] if a.code else [])
    bot = Assistant(llm=llm, function_list=tools,
                    system_message="You operate a DGX Spark lab. Use tools for facts and arithmetic. Be brief.")
    responses = []
    for responses in bot.run(messages=[{"role": "user", "content": a.question}]):
        pass
    for m in responses:                                   # the full trace: calls, results, answer
        if m.get("function_call"):
            print(f"[call]   {m['function_call']['name']}({m['function_call'].get('arguments', '')})")
        elif m.get("role") == "function":
            print(f"[result] {m.get('name')}: {str(m.get('content'))[:300]}")
        elif m.get("content"):
            print(f"[answer] {m['content']}")


if __name__ == "__main__":
    main()
