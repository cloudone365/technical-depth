#!/usr/bin/env python3
"""Scripted OpenAI endpoint for agent_tools.py tests: first reply asks for the
calculator tool, second reply (after seeing the tool result) answers."""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        tool_msgs = [m for m in req["messages"] if m["role"] == "tool"]
        if not tool_msgs:
            msg = {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "type": "function",
                   "function": {"name": "calculator", "arguments": json.dumps({"expression": "0.17 * 119.7"})}}]}
        else:
            val = json.loads(tool_msgs[-1]["content"])["result"]
            msg = {"role": "assistant", "content": f"17% of 119.7 GiB is {val:.2f} GiB."}
        body = json.dumps({"choices": [{"message": msg}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8767), H).serve_forever()
