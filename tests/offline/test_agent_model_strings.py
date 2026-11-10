"""Model replies with one string where a list or an object is expected: intake and router "avoid": "crowds",
"mustInclude": "Tokyo Tower", "dayTrips": "Nikko" and router "preferences": "vegetarian". Each is one item (or no
change), and the whole plan is still delivered. Runs against the fake Gateway (real Lambda code, mock mode)."""
from stubutil import sys_text
import base64, contextlib, io, json, os, re, sys, time, types
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): self.f = f; return f
    def run(self): pass
bac.BedrockAgentCoreApp = App; sys.modules["bedrock_agentcore"] = bac

SCEN = {"intake": {}, "router": {}}   # the bad fields of this scenario's intake and router replies
INTAKE = {"destination": "Tokyo", "destinationAirport": "HND", "originAirport": "SIN", "startDate": "2026-12-10",
          "endDate": "2026-12-14", "pax": 2, "budgetSGD": 6000, "interests": ["museum", "restaurant", "garden"],
          "activities": [], "pace": "relaxed", "avoid": [],
          "preferences": {"hotel": {"minStars": 4}, "food": {"coffee": True}}, "missing": [], "question": ""}


def text(t):
    return {"output": {"message": {"role": "assistant", "content": [{"text": t}]}},
            "usage": {"inputTokens": 1, "outputTokens": 1}, "stopReason": "end_turn"}


def converse(**k):
    sysm = sys_text(k)
    if sysm.startswith("ROLE: airport lookup"):
        return text('{"iata": "XXX"}')
    if sysm.startswith("ROLE: intake"):
        return text(json.dumps({**INTAKE, **SCEN["intake"]}))
    if sysm.startswith("ROLE: itinerary planner"):
        p = json.loads(re.search(r"Candidate pool \(the only valid IDs\):\n(\{.*?\})\n", sysm, re.S).group(1))
        ids = [x["id"] for x in p["places"]]
        days = [{"date": f"2026-12-{i}", "theme": f"D{i}", "placeIds": ids[2 * (i - 10):2 * (i - 10) + 2]}
                for i in range(10, 15)]
        return text(json.dumps({"flightOfferId": p["flights"][0]["id"], "stayId": p["stays"][0]["id"], "days": days,
                                "reasoning": "r"}))
    if sysm.startswith("ROLE: router"):
        return text(json.dumps({"intent": "modify", "specChanges": SCEN["router"], "instruction": "change it"}))
    if sysm.startswith("ROLE: itinerary repairer"):
        return text(re.search(r"Current plan:\n(\{.*?\})\n", sysm, re.S).group(1))
    if sysm.startswith("ROLE: independent reviewer"):
        return text('{"scores":{"preferenceFit":5,"theme":5,"pacing":5,"variety":5},"rationale":"good","suggestions":[]}')
    if sysm.startswith("ROLE: travel writer"):
        return text('{"title":"T","summary":"S","dayNotes":{}}')
    return text("ok")


class BR: converse = staticmethod(converse)
class SM:
    def get_secret_value(self, SecretId): return {"SecretString": json.dumps({"client_id": "a", "client_secret": "b"})}
class T:
    def __init__(s, n): s.n = n
    def put_item(s, Item): pass
class R:
    def Table(s, n): return T(n)


b = types.ModuleType("boto3")
b.client = lambda name, **k: {"bedrock-runtime": BR(), "secretsmanager": SM()}[name]
b.resource = lambda *a, **k: R()
sys.modules["boto3"] = b
os.environ.update(GATEWAY_URL="http://127.0.0.1:8765/mcp", GATEWAY_TOKEN_URL="http://127.0.0.1:8765/oauth2/token",
                  GATEWAY_SECRET_ID="x", TRIPS_TABLE="t", ITINERARIES_TABLE="i", JUDGE_MODEL_ID="nova")
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "agent"))
import tp_travel_agent as A

USER = "user-strings"
tok = "x." + base64.urlsafe_b64encode(json.dumps({"sub": USER, "exp": time.time() + 3600}).encode()).decode().rstrip("=") + ".y"


def run(name, intake=None, router=None):
    """One new session: the trip request, then (with a router reply) one change. Returns the session's spec."""
    SCEN["intake"], SCEN["router"] = intake or {}, router or {}
    sid = "tp-" + name.ljust(40, "s")[:40]
    ctx = types.SimpleNamespace(session_id=sid, request_headers={"Authorization": "Bearer " + tok})
    events = []
    for prompt in ["Tokyo 10-14 Dec 2026 for two, SGD 6000"] + (["please change it"] if router else []):
        with contextlib.redirect_stdout(io.StringIO()):
            events += list(A.invoke({"prompt": prompt}, ctx))
    errors = [e["message"] for e in events if e["type"] == "error"]
    assert not errors, (name, errors)
    assert any(e["type"] == "itinerary" for e in events), (name, [e["type"] for e in events])
    if router:
        assert any(e["type"] == "message" and "This is version 2" in e["message"] for e in events), (name, events[-1])
    print("   ", name, "delivered")
    return A.SESSIONS[A._session_key(USER, sid)]["spec"]


s = run("intake_avoid", intake={"avoid": "crowds"})
assert s["avoid"] == ["crowds"] and "crowds" in s["preferences"]["avoid"], (s["avoid"], s["preferences"]["avoid"])
s = run("intake_must", intake={"mustInclude": "Tokyo Tower"})
assert [m["name"] for m in s["mustInclude"]] == ["Tokyo Tower"], s["mustInclude"]
s = run("intake_daytrips", intake={"dayTrips": "Nikko"})
assert [t["place"] for t in s["dayTrips"]] == ["Nikko"], s["dayTrips"]
s = run("router_avoid", router={"avoid": "crowds"})
assert s["avoid"] == ["crowds"] and "crowds" in s["preferences"]["avoid"], (s["avoid"], s["preferences"]["avoid"])
s = run("router_must", router={"mustInclude": "Tokyo Tower"})
assert [m["name"] for m in s["mustInclude"]] == ["Tokyo Tower"], s["mustInclude"]
s = run("router_daytrips", router={"dayTrips": "Nikko"})
assert [t["place"] for t in s["dayTrips"]] == ["Nikko"], s["dayTrips"]
s = run("router_prefs", router={"preferences": "vegetarian"})
assert isinstance(s["preferences"], dict) and s["preferences"]["hotel"]["minStars"] == 4, s["preferences"]
print("MODEL STRINGS E2E OK")
