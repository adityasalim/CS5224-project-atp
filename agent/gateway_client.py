"""
Minimal MCP client for AgentCore Gateway (JSON-RPC over streamable HTTP), standard library only.

Gets an OAuth token from the Gateway's Cognito app client (client-credentials flow),
caches it until shortly before it expires, and calls tools by their short name
(e.g. "search_flights" resolves to "flights___search_flights").

Environment variables:
  GATEWAY_URL         https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
  GATEWAY_TOKEN_URL   https://<domain>.auth.<region>.amazoncognito.com/oauth2/token
  GATEWAY_SECRET_ID   Secrets Manager secret with {"client_id": "...", "client_secret": "..."}
  GATEWAY_SCOPE       optional OAuth scope
"""
import base64
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3


# MCP protocol versions to offer, newest first. AgentCore Gateway currently requires 2025-11-25.
PROTOCOL_VERSIONS = [v.strip() for v in os.environ.get(
    "MCP_PROTOCOL_VERSIONS", "2025-11-25,2025-06-18,2025-03-26").split(",") if v.strip()]


class GatewayError(RuntimeError):
    def __init__(self, message, rpc_error=None):
        super().__init__(message)
        self.rpc_error = rpc_error


class Gateway:
    def __init__(self):
        self.url = os.environ["GATEWAY_URL"]
        self.token_url = os.environ["GATEWAY_TOKEN_URL"]
        self.secret_id = os.environ["GATEWAY_SECRET_ID"]
        self.scope = os.environ.get("GATEWAY_SCOPE")
        self._token, self._expires = None, 0
        self._creds = None
        self._names = None
        self._tools = None
        self._id = 0
        self.version = None    # negotiated MCP protocol version
        self.session = None    # Mcp-Session-Id, if the Gateway issues one

    # ---------------------------------------------------------------- auth
    def _credentials(self):
        if self._creds is None:
            sm = boto3.client("secretsmanager")
            s = json.loads(sm.get_secret_value(SecretId=self.secret_id)["SecretString"])
            self._creds = (s["client_id"], s["client_secret"])
        return self._creds

    def _get_token(self, force=False):
        if not force and self._token and time.time() < self._expires - 60:
            return self._token
        cid, secret = self._credentials()
        form = {"grant_type": "client_credentials"}
        if self.scope:
            form["scope"] = self.scope
        req = urllib.request.Request(
            self.token_url, data=urllib.parse.urlencode(form).encode(), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Authorization": "Basic " + base64.b64encode(f"{cid}:{secret}".encode()).decode()})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            raise GatewayError(f"token request failed {e.code}: {e.read().decode(errors='replace')[:300]}")
        except (OSError, http.client.HTTPException, ValueError) as e:   # unreachable, timeout, not JSON
            raise GatewayError(f"token request failed: {type(e).__name__}: {e}")
        self._token = data["access_token"]
        self._expires = time.time() + int(data.get("expires_in", 3600))
        return self._token

    # ---------------------------------------------------------------- JSON-RPC
    def _post(self, msg, retry=True):
        headers = {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.version:
            headers["MCP-Protocol-Version"] = self.version
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                if r.headers.get("Mcp-Session-Id"):
                    self.session = r.headers["Mcp-Session-Id"]
                return r.headers.get("Content-Type", ""), r.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code in (401, 403) and retry:
                self._get_token(force=True)
                return self._post(msg, retry=False)
            if e.code == 404 and self.session and retry:   # session expired -> handshake again
                self.session, self.version = None, None
                self._initialize()
                return self._post(msg, retry=False)
            try:
                err = json.loads(body).get("error")
            except (json.JSONDecodeError, AttributeError):
                err = None
            raise GatewayError(f"{msg.get('method')} failed {e.code}: {body[:300]}", err)
        except urllib.error.URLError as e:
            raise GatewayError(f"cannot reach gateway: {e.reason}")
        except (OSError, http.client.HTTPException, UnicodeDecodeError) as e:   # read timeout, reset, cut-off body
            raise GatewayError(f"{msg.get('method')} failed: {type(e).__name__}: {e}")

    def _rpc(self, method, params=None):
        if self.version is None and method != "initialize":
            self._initialize()
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        ctype, body = self._post(msg)
        try:
            if "text/event-stream" in ctype or body.lstrip().startswith(("event:", "data:")):
                msgs = [json.loads(l[5:].strip()) for l in body.splitlines() if l.startswith("data:") and l[5:].strip()]
                out = next((m for m in reversed(msgs) if isinstance(m, dict) and ("result" in m or "error" in m)), {})
            else:
                out = json.loads(body) if body.strip() else {}
        except ValueError as e:   # an HTML error page or a cut-off reply
            raise GatewayError(f"{method} returned a reply that is not JSON: {e}")
        if not isinstance(out, dict):
            raise GatewayError(f"{method} returned a reply that is not a JSON object")
        if "error" in out:
            raise GatewayError(f"{method} error: {json.dumps(out['error'])[:300]}", out["error"])
        return out.get("result", {})

    def _initialize(self):
        """MCP handshake: offer the newest protocol version; if the Gateway lists the versions it
        supports, retry with those. Every later request then sends MCP-Protocol-Version."""
        offers, last = list(PROTOCOL_VERSIONS), None
        while offers:
            v = offers.pop(0)
            try:
                res = self._rpc("initialize", {"protocolVersion": v, "capabilities": {},
                                               "clientInfo": {"name": "tp-travel-agent", "version": "1.1"}})
            except GatewayError as e:
                last = e
                data = (e.rpc_error or {}).get("data") if isinstance(e.rpc_error, dict) else None
                supported = (data or {}).get("supported") or []
                offers = [x for x in supported if x != v and x not in offers] + offers
                continue
            self.version = res.get("protocolVersion") or v
            try:
                self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
            except GatewayError:
                pass
            return res
        raise last or GatewayError("MCP initialize failed")

    # ---------------------------------------------------------------- tools
    def tools(self):
        """Full tool list from the Gateway (cached)."""
        if self._tools is None:
            self._tools = self._rpc("tools/list").get("tools", [])
            self._names = {}
            for t in self._tools:
                short = t["name"].split("___")[-1]
                self._names[short] = t["name"]
        return self._tools

    def full_name(self, short):
        self.tools()
        if short not in self._names:
            raise GatewayError(f"tool '{short}' not found on the gateway; have {sorted(self._names)}")
        return self._names[short]

    def call(self, short_or_full, arguments):
        """Calls a tool and returns the Lambda's JSON output as a dict."""
        name = short_or_full if "___" in short_or_full else self.full_name(short_or_full)
        t0, res = time.monotonic(), None
        try:
            res = self._rpc("tools/call", {"name": name, "arguments": arguments})
        finally:   # per-tool timing for latency work (also when the call fails)
            print(json.dumps({"stage": "tool", "tool": name.split("___")[-1], "ms": round((time.monotonic() - t0) * 1000),
                              "failed": res is None}))
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError:
            data = {"ok": False, "error": "non_json_result", "message": text[:300]}
        if res.get("isError") and data.get("ok") is not False:
            data = {"ok": False, "error": "tool_error", "message": text[:300]}
        return data
