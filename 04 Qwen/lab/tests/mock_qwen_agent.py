#!/usr/bin/env python3
"""OpenAI-compatible stand-in for qwen_agent_demo.py (Volume 16). Qwen-Agent's 'nous'
function-calling format puts tool descriptions in the prompt and expects <tool_call>
blocks in the reply. First turn: call the calculator. After a tool response: answer."""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        flat = json.dumps(req["messages"])
        if "tool_response" in flat or '"role": "tool"' in flat or '"role": "function"' in flat:
            text = "3 of 4 slices is 75.0%."
        else:
            text = '<tool_call>\n{"name": "calculator", "arguments": {"expression": "3/4*100"}}\n</tool_call>'
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunk = {"id": "x", "object": "chat.completion.chunk", "created": int(time.time()), "model": req["model"],
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            chunk["choices"][0] = {"index": 0, "delta": {}, "finish_reason": "stop"}
            self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
            return
        b = json.dumps({"id": "x", "object": "chat.completion", "created": int(time.time()), "model": req["model"],
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
