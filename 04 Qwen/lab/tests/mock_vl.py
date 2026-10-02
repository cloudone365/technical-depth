#!/usr/bin/env python3
"""Qwen2.5-VL stand-in for vl_eval.py (Volume 05) and mm_rag.py (Volume 19).
Decodes the data-URL PNG and reads what the generators stored in its text chunks:
  "transcript" + a "Transcribe" prompt → the page text (mm_rag ingest)
  "qa" (question → answer)             → the right answer (mm_rag ask/eval)
  "answer"                             → right on every other call, so vl_eval scores 50 %
Also checks the OpenAI vision message shape."""
import base64
import io
import itertools
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

N = itertools.count()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        parts = req["messages"][0]["content"]
        url = next(p["image_url"]["url"] for p in parts if p["type"] == "image_url")
        assert url.startswith("data:image/png;base64,")
        img = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
        text = next(p["text"] for p in parts if p["type"] == "text")
        if "transcript" in img.info and text.startswith("Transcribe"):
            ans = img.info["transcript"]
        elif "qa" in img.info:
            ans = json.loads(img.info["qa"]).get(text, "unknown")
        else:
            ans = img.info["answer"] if next(N) % 2 == 0 else "?"
        b = json.dumps({"choices": [{"message": {"role": "assistant", "content": ans}}],
                        "usage": {"prompt_tokens": 300}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
