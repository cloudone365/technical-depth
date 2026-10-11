#!/usr/bin/env python3
"""Minimal prefill/decode proxy for vLLM NixlConnector (Chapter 23).

Mirrors vLLM's tests/v1/kv_connector/nixl_integration/toy_proxy_server.py:
 1. send the request to the PREFILL server with max_tokens=1, stream=False and
    kv_transfer_params.do_remote_decode=True  → prefill computes the KV cache
 2. copy the returned kv_transfer_params into the request
 3. send it to the DECODE server, which pulls the KV blocks over NIXL/UCX and
    generates the rest; its response (streamed or not) goes back to the client.
Stdlib only. Env: PREFILL_URL, DECODE_URL, PORT.
"""
import copy
import http.client
import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFILL = urllib.parse.urlparse(os.environ.get("PREFILL_URL", "http://vllm-prefill:8000"))
DECODE = urllib.parse.urlparse(os.environ.get("DECODE_URL", "http://vllm-decode:8000"))


def post(url, path, body):
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=600)
    conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
    return conn, conn.getresponse()


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        pre = copy.deepcopy(req)
        pre.update(stream=False, max_tokens=1)
        pre.pop("stream_options", None)
        pre.pop("max_completion_tokens", None)
        pre["kv_transfer_params"] = {"do_remote_decode": True, "do_remote_prefill": False,
                                     "remote_engine_id": None, "remote_block_ids": None,
                                     "remote_host": None, "remote_port": None}
        t0 = time.perf_counter()
        c1, r1 = post(PREFILL, self.path, pre)
        pre_resp = json.loads(r1.read())
        c1.close()
        ttft_ms = (time.perf_counter() - t0) * 1e3
        if pre_resp.get("kv_transfer_params"):
            req["kv_transfer_params"] = pre_resp["kv_transfer_params"]
        c2, r2 = post(DECODE, self.path, req)
        self.send_response(r2.status)
        for k in ("Content-Type",):
            if r2.getheader(k):
                self.send_header(k, r2.getheader(k))
        self.send_header("X-Prefill-Ms", f"{ttft_ms:.1f}")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        while True:
            chunk = r2.read1(65536)
            if not chunk:
                break
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        c2.close()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8000"))), Proxy).serve_forever()
