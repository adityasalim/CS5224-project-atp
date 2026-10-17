"""Fake AgentCore Gateway (MCP over HTTP on 127.0.0.1:8765) that runs the real Lambda code in mock mode."""
import json, sys, glob, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import os, pathlib
sys.path.insert(0, os.path.dirname(__file__))
from fakes import load, REPO   # fake boto3/fpdf + load()
TARGETS = {"flights": "tp-search-flights", "stays": "tp-search-stays", "places": "tp-find-places", "routes": "tp-route-matrix",
           "sequence": "tp-sequence-day", "verify": "tp-verify-itinerary", "pdf": "tp-render-pdf", "transfers": "tp-search-transfers"}
MODS = {t: load(fn) for t, fn in TARGETS.items()}
TOOLS = []
for t, fn in TARGETS.items():
    sch = json.load(open(REPO / "lambdas" / "schemas" / f"{fn}.json"))
    TOOLS.append({**sch, "name": f"{t}___{sch['name']}"})
SSE = {"n": 0}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.endswith("openid-configuration"):
            return self._send(200, "application/json", json.dumps({"issuer": "x", "token_endpoint": "http://127.0.0.1:8765/oauth2/token"}))
        self._send(404, "text/plain", "nope")
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if self.path == "/oauth2/token":
            assert self.headers["Authorization"].startswith("Basic ")
            return self._send(200, "application/json", json.dumps({"access_token": "tok123", "token_type": "Bearer"}))
        if self.headers.get("Authorization") != "Bearer tok123":
            return self._send(401, "application/json", '{"message":"Unauthorized"}')
        m = json.loads(body)
        def bad(req):
            return self._send(400, "application/json", json.dumps({"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32600,
                "message": f"Unsupported protocol version: {req}", "data": {"supported": ["2025-11-25"], "requested": req}}}))
        if m["method"] == "initialize":
            req = m["params"]["protocolVersion"]
            if req != "2025-11-25": return bad(req)
            res = {"serverInfo": {"name": "tp-dev-gateway"}, "protocolVersion": "2025-11-25"}
        elif "id" not in m:
            return self._send(202, "application/json", "")
        elif self.headers.get("MCP-Protocol-Version", "2025-03-26") != "2025-11-25":
            return bad(self.headers.get("MCP-Protocol-Version", "2025-03-26"))
        if m["method"] == "initialize": pass
        elif m["method"] == "tools/list": res = {"tools": TOOLS}
        else:
            t, _ = m["params"]["name"].split("___")
            out = MODS[t].lambda_handler(m["params"]["arguments"], None)
            res = {"content": [{"type": "text", "text": json.dumps(out)}], "isError": False}
        msg = json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": res})
        SSE["n"] += 1
        if SSE["n"] % 2: self._send(200, "text/event-stream", f"event: message\ndata: {msg}\n\n")
        else: self._send(200, "application/json", msg)
    def _send(self, code, ctype, text):
        self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(text.encode())
HTTPServer(("127.0.0.1", 8765), H).serve_forever()
