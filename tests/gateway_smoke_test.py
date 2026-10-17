#!/usr/bin/env python3
"""
gateway_smoke_test.py — check that AgentCore Gateway exposes your 8 Lambda tools over MCP
and that each one answers, BEFORE any LLM is involved.

What it does:
  1. Gets an access token from the Gateway's Cognito app client (client-credentials flow).
  2. Calls MCP "tools/list" and checks all 8 tools are there.
  3. Calls each tool in order with the honeymoon test data:
     search_flights -> search_stays -> find_places -> route_matrix -> search_transfers -> sequence_day
     -> verify_itinerary -> render_pdf
     (later tools use IDs returned by earlier ones, exactly like the agent will).

Runs in AWS CloudShell with no installs (standard library only; boto3 is optional and
only used for --secret).

Usage (CloudShell):
  python3 gateway_smoke_test.py \
      --gateway-url "https://<gateway-id>.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp" \
      --token-url   "https://<your-domain>.auth.ap-southeast-1.amazoncognito.com/oauth2/token" \
      --client-id   "<app client id>" \
      --client-secret "<app client secret>"

  Or keep the client id/secret in Secrets Manager as {"client_id": "...", "client_secret": "..."}:
  python3 gateway_smoke_test.py --gateway-url ... --token-url ... --secret tp/dev/gateway-client

  Easiest: let the script find the token URL from the Gateway's Discovery URL:
  python3 gateway_smoke_test.py --gateway-url ... \
      --discovery-url "https://cognito-idp.ap-southeast-1.amazonaws.com/<pool-id>/.well-known/openid-configuration" \
      --secret tp/dev/gateway-client

  Add --list-only to stop after tools/list.  Add --scope "<scope>" if your token request needs one.
"""
import argparse
import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

# MCP protocol versions to offer, newest first. AgentCore Gateway currently requires 2025-11-25.
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
EXPECTED = ["search_flights", "search_stays", "find_places", "route_matrix",
            "search_transfers", "sequence_day", "verify_itinerary", "render_pdf"]
OK, BAD = "✅", "❌"


# ---------------------------------------------------------------- HTTP helpers
def _check_url(url, label):
    p = urllib.parse.urlparse(url.strip())
    if p.scheme not in ("http", "https") or not p.netloc:
        sys.exit(f"{BAD} {label} doesn't look like a URL: {url!r}\n   It must start with https://")
    if "<" in url or ">" in url or " " in url.strip():
        sys.exit(f"{BAD} {label} still contains a placeholder or space: {url!r}\n"
                 "   Replace the <...> part with your real value.")
    return url.strip()


def _unreachable(url, err):
    host = urllib.parse.urlparse(url).netloc
    sys.exit(f"{BAD} Could not reach {host}\n   ({err})\n"
             "   This host name doesn't exist or can't be reached. Check for typos or leftover placeholders.\n"
             "   For the token URL, use --discovery-url instead and the script will find it for you.")


class MCPError(RuntimeError):
    def __init__(self, error, status=None):
        self.error, self.status = error, status
        super().__init__(json.dumps(error)[:500])


def http_get(url, timeout=20):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        _unreachable(url, e.reason)


def http_post(url, data, headers, timeout=90):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode(errors="replace")
    except urllib.error.URLError as e:
        _unreachable(url, e.reason)


def token_url_from_discovery(discovery_url):
    status, body = http_get(discovery_url)
    if status != 200:
        sys.exit(f"{BAD} Discovery URL returned {status}: {body[:200]}")
    token_url = json.loads(body).get("token_endpoint")
    if not token_url:
        sys.exit(f"{BAD} The discovery document has no token_endpoint.\n"
                 "   The user pool has no domain yet: Cognito -> your pool -> Branding -> Domain -> "
                 "create a Cognito domain, then run again.")
    return token_url


def get_token(token_url, client_id, client_secret, scope=None):
    form = {"grant_type": "client_credentials"}
    if scope:
        form["scope"] = scope
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    status, _, body = http_post(token_url, urllib.parse.urlencode(form).encode(),
                                {"Content-Type": "application/x-www-form-urlencoded",
                                 "Authorization": f"Basic {basic}"}, timeout=20)
    if status != 200:
        sys.exit(f"{BAD} Token request failed ({status}): {body[:300]}\n"
                 "   Check --token-url (must end in /oauth2/token), client id and secret.")
    return json.loads(body)["access_token"]


class MCP:
    def __init__(self, url, token, version=None):
        self.url, self.token, self.n = url, token, 0
        self.session = None
        self.version = version          # negotiated MCP protocol version

    def _headers(self):
        h = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        if self.version:
            h["MCP-Protocol-Version"] = self.version
        if self.session:
            h["Mcp-Session-Id"] = self.session
        return h

    def _send(self, msg):
        status, headers, body = http_post(self.url, json.dumps(msg).encode(), self._headers())
        sid = headers.get("Mcp-Session-Id") if headers else None
        if sid:
            self.session = sid
        if status in (401, 403):
            sys.exit(f"{BAD} Gateway refused the token ({status}): {body[:300]}\n"
                     "   The client id must be in the Gateway's allowed clients (Inbound auth).")
        ctype = headers.get("Content-Type", "") if headers else ""
        if status >= 400:
            try:
                err = json.loads(body).get("error")
            except (json.JSONDecodeError, AttributeError):
                err = None
            if err:
                raise MCPError(err, status)
            sys.exit(f"{BAD} {msg.get('method')} failed ({status}): {body[:500]}")
        return ctype, body

    def call(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        ctype, body = self._send(msg)
        return self._parse(ctype, body)

    def notify(self, method):
        try:
            self._send({"jsonrpc": "2.0", "method": method})
        except MCPError:
            pass  # notifications are fire-and-forget

    def initialize(self):
        """MCP handshake. Offers the newest version first; if the server names the versions it
        supports, retries with one of those. All later requests carry MCP-Protocol-Version."""
        offers = [self.version] if self.version else list(PROTOCOL_VERSIONS)
        last = None
        while offers:
            v = offers.pop(0)
            try:
                self.version = None  # the handshake itself is sent without the header
                res = self.call("initialize", {"protocolVersion": v, "capabilities": {},
                                               "clientInfo": {"name": "tp-smoke-test", "version": "1.1"}})
            except MCPError as e:
                last = e
                supported = (e.error.get("data") or {}).get("supported") if isinstance(e.error, dict) else None
                if supported:
                    offers = [x for x in supported if x != v] + offers
                continue
            self.version = res.get("protocolVersion") or v
            self.notify("notifications/initialized")
            return res
        raise last or MCPError({"message": "initialize failed"})

    @staticmethod
    def _parse(ctype, body):
        if "text/event-stream" in ctype or body.lstrip().startswith(("event:", "data:")):
            msgs = [json.loads(l[5:].strip()) for l in body.splitlines()
                    if l.startswith("data:") and l[5:].strip()]
            body_msg = next((m for m in reversed(msgs) if "result" in m or "error" in m), msgs[-1] if msgs else {})
        else:
            body_msg = json.loads(body) if body.strip() else {}
        if "error" in body_msg:
            raise MCPError(body_msg["error"])
        return body_msg.get("result", {})


def tool_result(result):
    """MCP tools/call result -> the Lambda's JSON output (dict), plus isError flag."""
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    raw = "".join(texts)
    try:
        return json.loads(raw), bool(result.get("isError"))
    except json.JSONDecodeError:
        return {"raw": raw}, bool(result.get("isError"))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gateway-url", required=True, help="Gateway URL ending in /mcp")
    ap.add_argument("--token-url", help="Cognito URL ending in /oauth2/token")
    ap.add_argument("--discovery-url", help="Gateway's inbound-auth Discovery URL (alternative to --token-url)")
    ap.add_argument("--client-id")
    ap.add_argument("--client-secret")
    ap.add_argument("--secret", help="Secrets Manager name/ARN holding client_id and client_secret")
    ap.add_argument("--scope", help="optional OAuth scope")
    ap.add_argument("--region", default="ap-southeast-1")
    ap.add_argument("--list-only", action="store_true")
    ap.add_argument("--protocol-version", help="force an MCP protocol version, e.g. 2025-11-25")
    a = ap.parse_args()

    if a.secret:
        import boto3  # available in CloudShell
        s = json.loads(boto3.client("secretsmanager", region_name=a.region)
                       .get_secret_value(SecretId=a.secret)["SecretString"])
        a.client_id = a.client_id or s.get("client_id")
        a.client_secret = a.client_secret or s.get("client_secret")
    if not (a.client_id and a.client_secret):
        sys.exit("Give --client-id and --client-secret, or --secret.")
    a.gateway_url = _check_url(a.gateway_url, "--gateway-url")
    if a.discovery_url:
        a.discovery_url = _check_url(a.discovery_url, "--discovery-url")
        print("0) Reading the Discovery URL ...")
        a.token_url = token_url_from_discovery(a.discovery_url)
        print(f"   {OK} token URL is {a.token_url}")
    if not a.token_url:
        sys.exit("Give --token-url or --discovery-url.")
    a.token_url = _check_url(a.token_url, "--token-url")

    print("1) Getting a token from Cognito ...")
    token = get_token(a.token_url, a.client_id, a.client_secret, a.scope)
    print(f"   {OK} got access token ({len(token)} chars)")

    mcp = MCP(a.gateway_url, token, a.protocol_version)
    info = mcp.initialize()
    print(f"   {OK} connected to {info.get('serverInfo', {}).get('name', 'gateway')} "
          f"(MCP protocol {mcp.version})")

    print("2) Listing tools (tools/list) ...")
    tools = mcp.call("tools/list").get("tools", [])
    names = sorted(t["name"] for t in tools)
    for n in names:
        print(f"   - {n}")
    full = {}
    for short in EXPECTED:
        match = [n for n in names if n == short or n.endswith("___" + short)]
        if match:
            full[short] = match[0]
    missing = [s for s in EXPECTED if s not in full]
    if missing:
        print(f"   {BAD} missing tools: {missing}  (add those targets, or check their schema 'name')")
    else:
        print(f"   {OK} all {len(EXPECTED)} tools found")
    if a.list_only or missing:
        sys.exit(1 if missing else 0)

    print("3) Calling each tool ...")
    passed = 0

    def run(short, args, check):
        nonlocal passed
        res, is_err = tool_result(mcp.call("tools/call", {"name": full[short], "arguments": args}))
        good = (not is_err) and res.get("ok") is True and check(res)
        detail = res.get("source") or res.get("error") or ""
        print(f"   {OK if good else BAD} {short:<17} {detail}")
        if not good:
            print("      " + json.dumps(res)[:600])
        passed += good
        return res

    trip = {"startDate": "2026-12-10", "endDate": "2026-12-15", "pax": 2, "budgetSGD": 5000,
            "hardConstraints": {"maxStopsPerDay": 6, "minFreeMinutesPerDay": 60}}
    fl = run("search_flights", {"origin": "SIN", "destination": "HND", "departDate": trip["startDate"],
                                "returnDate": trip["endDate"], "pax": 2, "cabin": "economy"},
             lambda r: len(r.get("offers", [])) > 0)
    st = run("search_stays", {"city": "Tokyo", "checkIn": trip["startDate"], "checkOut": trip["endDate"],
                              "guests": 2, "maxPricePerNightSGD": 400}, lambda r: len(r.get("stays", [])) > 0)
    pl = run("find_places", {"city": "Tokyo", "categories": ["temple", "museum", "restaurant"], "radiusKm": 4},
             lambda r: len(r.get("places", [])) >= 3)
    places = pl.get("places", [])
    run("route_matrix", {"points": [{"id": p["placeId"], "lat": p["lat"], "lng": p["lng"]} for p in places[:3]],
                         "mode": "walk"} if len(places) >= 2 else
        {"points": [{"id": "a", "lat": 35.68, "lng": 139.76}, {"id": "b", "lat": 35.71, "lng": 139.79}]},
        lambda r: len(r.get("minutes", [])) >= 2)
    run("search_transfers", {"from": {"city": "Copenhagen", "airport": "CPH"}, "to": {"city": "Lofoten", "airport": "BOO"},
                             "date": "2026-12-13", "pax": 2}, lambda r: len(r.get("options", [])) > 0)

    if not (fl.get("offers") and st.get("stays") and places):
        sys.exit(f"Stopping: search tools returned no data, so the chained tools can't be tested. "
                 f"{passed}/5 passed so far.")
    stay_id, offer_id = st["stays"][0]["stayId"], fl["offers"][0]["offerId"]
    day_ids = [p["placeId"] for p in places[:3]]
    sq = run("sequence_day", {"date": "2026-12-11", "stayId": stay_id, "placeIds": day_ids,
                              "dayStart": "09:00", "dayEnd": "21:00"}, lambda r: len(r.get("stops", [])) > 0)
    stops = [{k: s[k] for k in ("placeId", "arriveAt", "departAt", "travelMinutesFromPrev")}
             for s in sq.get("stops", [])]
    days = [{"date": d, "stops": stops if d == "2026-12-11" else []}
            for d in ["2026-12-10", "2026-12-11", "2026-12-12", "2026-12-13", "2026-12-14", "2026-12-15"]]
    itinerary = {"flightOfferId": offer_id, "stayId": stay_id, "days": days}
    vf = run("verify_itinerary", {"tripSpec": trip, "itinerary": itinerary}, lambda r: "passed" in r)
    if vf.get("ok"):
        print(f"      verifier says passed={vf.get('passed')}, total SGD {vf.get('totals', {}).get('totalSGD')}")
        for v in vf.get("violations", [])[:5]:
            print(f"      - {v['check']}: {v['message']}")
    pdf = run("render_pdf", {"tripId": "smoke_test", "title": "Smoke test plan", "tripSpec": trip,
                             "itinerary": itinerary}, lambda r: bool(r.get("url")))
    if pdf.get("url"):
        print("      PDF link (copy the WHOLE line below; it works for 15 minutes):")
        print(pdf["url"])
        if pdf.get("s3Key"):
            print(f"      Also in S3: {pdf['s3Key']}")

    print(f"\nResult: {passed}/8 tools answered correctly through the Gateway.")
    sys.exit(0 if passed == 8 else 1)


if __name__ == "__main__":
    try:
        main()
    except MCPError as e:
        sys.exit(f"{BAD} The Gateway returned an MCP error: {e}")
