"""
tp_travel_agent — the travel-planning agent hosted on Amazon Bedrock AgentCore Runtime.

LLM-Modulo loop (report Figure 2):
  1 Intake    (Haiku 5.5)    request -> TripSpec JSON, or one clarifying question
  2 Gather    (Sonnet+tools) the model chooses search_flights / search_stays / find_places via the Gateway
  3 Propose   (Sonnet 5.5)   picks only candidate-pool IDs -> day-by-day plan (adaptive thinking)
  4 Sequence  (code tool)    sequence_day orders each day around opening hours + travel times
  5 Verify    (code tool)    verify_itinerary checks budget, dates, hours, travel, pacing (prices from pool)
  6 Judge     (Nova)         scores preference fit, theme, pacing, variety (1-5)
  7 Repair    (Sonnet)       fixes failing parts; back to 4; at most MAX_REPAIR_ROUNDS
  8 Deliver                  narrative (+ Guardrails check), render_pdf, save to DynamoDB, stream result

Streams JSON events, one per line:
  {"type": "status", "message": "..."}       progress
  {"type": "question", "message": "..."}     clarifying question (reply in the same session)
  {"type": "itinerary", "itinerary": {...}}  final result (format the web page expects)
  {"type": "error", "message": "..."}

Environment variables: see README.md (GATEWAY_*, *_MODEL_ID, TRIPS_TABLE, ITINERARIES_TABLE, ...).
"""
import base64
import json
import os
import re
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone

import boto3
from bedrock_agentcore import BedrockAgentCoreApp

from gateway_client import Gateway, GatewayError
from prompts import get_prompt

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
# Models (global cross-Region inference profiles, callable from ap-southeast-1):
#   planner/repair: Claude Sonnet 5.5 with adaptive thinking  (fallback: Sonnet 5, then Sonnet 4.6)
#   intake/router/chat/narrative: Claude Haiku 5.5, thinking off (fallback: Haiku 4.5)
#   judge: Amazon Nova 2 Lite, a different model family so the reviewer doesn't grade its own family's work
PLANNER_MODEL = os.environ.get("PLANNER_MODEL_ID", "global.anthropic.claude-sonnet-5-5")
INTAKE_MODEL = os.environ.get("INTAKE_MODEL_ID", "global.anthropic.claude-haiku-5-5")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL_ID") or "global.amazon.nova-2-lite-v1:0"
PLANNER_EFFORT = os.environ.get("PLANNER_EFFORT", "medium").lower()   # low | medium | high | off
# If a model isn't enabled for the account (or the ID is unknown), switch once to the next one in its chain.
FALLBACK_CHAINS = [
    [PLANNER_MODEL] + [m for m in os.environ.get(
        "PLANNER_FALLBACK_MODEL_IDS", "global.anthropic.claude-sonnet-5,global.anthropic.claude-sonnet-4-6").split(",") if m],
    [INTAKE_MODEL] + [m for m in os.environ.get(
        "INTAKE_FALLBACK_MODEL_IDS", "global.anthropic.claude-haiku-4-5-20251001-v1:0").split(",") if m],
    [JUDGE_MODEL] + [m for m in os.environ.get("JUDGE_FALLBACK_MODEL_IDS", "").split(",") if m],
]
MAX_REPAIR_ROUNDS = int(os.environ.get("MAX_REPAIR_ROUNDS", "3"))
LOOP_ENABLED = os.environ.get("LOOP", "true").lower() == "true"   # "false" = ablation variant B
DAILY_SPEND = float(os.environ.get("DAILY_SPEND_SGD_PER_PERSON", "100"))   # same as the verifier
JUDGE_PASS = int(os.environ.get("JUDGE_PASS_SCORE", "4"))
HOME_AIRPORT = os.environ.get("DEFAULT_HOME_AIRPORT", "SIN")
GATHER_TOOLS = ("search_flights", "search_stays", "find_places")
PLACE_CATEGORIES = {"temple", "shrine", "church", "museum", "gallery", "attraction", "viewpoint", "park", "garden",
                    "zoo", "market", "shopping", "restaurant", "cafe", "bar", "climbing", "hiking", "nature", "onsen",
                    "beach", "aquarium", "theme_park", "historic", "nightlife", "dessert", "street_food", "spa",
                    "bookshop", "playground"}
OUTDOOR = {"climbing", "hiking", "nature", "onsen", "beach"}
ARRIVAL_BUFFER_MIN, DEPARTURE_BUFFER_MIN = 120, 180
TRANSFER_BUFFER_MIN = 60   # same as the verifier

_bedrock = None
_dynamodb = None
_gateway = None
SESSIONS = {}   # session_id -> {"spec": ..., "request": ...} (Runtime sessions are sticky)

# ---- AgentCore Memory (Phase 6.4). Off unless MEMORY_ID is set.
MEMORY_ID = os.environ.get("MEMORY_ID", "").strip()
PREF_NAMESPACE = os.environ.get("MEMORY_PREFERENCES_NAMESPACE", "/users/{actorId}/preferences")
_memory = None


def _mem():
    global _memory
    if _memory is None:
        _memory = boto3.client("bedrock-agentcore", region_name=REGION)
    return _memory


def _memory_ok(user_id, session_id):
    # Only for signed-in users; IDs must match AgentCore's allowed characters.
    return bool(MEMORY_ID) and user_id not in ("anonymous", "", None) and \
        re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-_]{0,99}", session_id or "") is not None


def memory_save(user_id, session_id, turns):
    """Short-term memory: store this turn's messages as conversation events. Long-term strategies
    (user preferences, summaries) are extracted from these events by AgentCore in the background."""
    if not (_memory_ok(user_id, session_id) and turns):
        return
    payload = [{"conversational": {"content": {"text": text[:4000]}, "role": role}} for role, text in turns if text]
    try:
        _mem().create_event(memoryId=MEMORY_ID, actorId=user_id, sessionId=session_id,
                            eventTimestamp=datetime.now(timezone.utc), payload=payload[:100])
    except Exception as e:  # noqa: BLE001 — memory must never break planning
        print("memory create_event failed:", e)


def memory_recall(user_id, session_id, query):
    """Long-term memory: the traveller's learned preferences from earlier conversations."""
    if not _memory_ok(user_id, session_id):
        return []
    try:
        r = _mem().retrieve_memory_records(
            memoryId=MEMORY_ID, namespace=PREF_NAMESPACE.replace("{actorId}", user_id),
            searchCriteria={"searchQuery": query[:500] or "travel preferences", "topK": 5}, maxResults=5)
    except Exception as e:  # noqa: BLE001
        print("memory retrieve failed:", e)
        return []
    out = []
    for rec in r.get("memoryRecordSummaries", []):
        text = ((rec.get("content") or {}).get("text") or "").strip()
        if text:
            try:   # the user-preference strategy stores JSON like {"preference": "...", "context": "..."}
                j = json.loads(text)
                text = j.get("preference") or text
            except (json.JSONDecodeError, AttributeError):
                pass
            out.append(text[:300])
    return out


# ====================================================================== small helpers
def _clients():
    global _bedrock, _dynamodb, _gateway
    if _bedrock is None:
        _bedrock = boto3.client("bedrock-runtime", region_name=REGION)
        _dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _gateway = Gateway()
    return _bedrock, _dynamodb, _gateway


def ev(kind, message=None, **extra):
    e = {"type": kind, **extra}
    if message is not None:
        e["message"] = message
    return e


def extract_json(text):
    """Pulls the first JSON object out of a model reply (handles ```json fences and chatter)."""
    if not text:
        raise ValueError("empty reply")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else text
    start = candidate.find("{")
    if start < 0:
        raise ValueError("no JSON object in reply")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(candidate[start:], start):
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            elif ch != "\\":
                esc = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(candidate[start:i + 1])
    raise ValueError("unbalanced JSON in reply")


def _hm(s):
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def _fmt(mins):
    mins = max(0, min(int(mins), 23 * 60 + 59))
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _user_from_context(context, payload):
    """User ID = Cognito 'sub' from the JWT the Runtime already validated (needs the Authorization
    header in the Runtime's request-header allowlist). Falls back for IAM-invoked tests."""
    headers = {}
    try:
        headers = {k.lower(): v for k, v in (getattr(context, "request_headers", None) or {}).items()}
    except Exception:  # noqa: BLE001
        headers = {}
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            part = auth.split()[1].split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            if claims.get("sub"):
                return claims["sub"]
        except Exception:  # noqa: BLE001
            pass
    if os.environ.get("ALLOW_PAYLOAD_USER", "false").lower() == "true" and payload.get("userId"):
        return str(payload["userId"])
    return "anonymous"


class Usage:
    def __init__(self):
        self.input = self.output = self.calls = self.cache_read = self.cache_write = 0

    def add(self, resp):
        u = resp.get("usage", {})
        self.input += u.get("inputTokens", 0)
        self.output += u.get("outputTokens", 0)
        self.cache_read += u.get("cacheReadInputTokens", 0)
        self.cache_write += u.get("cacheWriteInputTokens", 0)
        self.calls += 1


_SWAPPED = {}      # model asked for -> model actually used (after an access/ID error)
_SHAPE_OK = {}     # (model, think) -> index of the request shape that worked
_NO_CACHE = set()  # models that rejected prompt-cache checkpoints
PROMPT_CACHE = os.environ.get("PROMPT_CACHE", "true").lower() == "true"
CACHE_POINT = {"cachePoint": {"type": "default"}}


def _can_cache(model):
    return PROMPT_CACHE and model not in _NO_CACHE and bool(re.search(r"anthropic\.claude|amazon\.nova", model))


def _request_parts(model, system, messages, tools, cache):
    """System blocks, messages and tools for Converse. system may be one string or [shared_context, role_text]:
    with caching on, a cache checkpoint goes after the shared context (the trip + candidate pool), after the
    tool list and after the newest message, so repeated planning/repair calls and the gather loop re-read those
    tokens from the prompt cache (about 10% of the normal input price) instead of paying for them again."""
    parts = [system] if isinstance(system, str) else [x for x in system if x]
    use = cache and _can_cache(model)
    blocks = []
    for i, text in enumerate(parts):
        blocks.append({"text": text})
        if use and i == 0 and (len(parts) > 1 or tools):
            blocks.append(dict(CACHE_POINT))
    tool_list = list(tools or [])
    if use and tool_list:
        tool_list.append(dict(CACHE_POINT))
    msgs = messages
    if use and tools and messages and messages[-1]["role"] == "user":
        msgs = messages[:-1] + [{**messages[-1], "content": list(messages[-1]["content"]) + [dict(CACHE_POINT)]}]
    return blocks, msgs, tool_list


def effective_model(model):
    return _SWAPPED.get(model, model)


def _adaptive(model):
    """Claude 5-series models think adaptively by default; older ones and Nova don't take these fields."""
    return bool(re.search(r"anthropic\.claude-(sonnet|haiku|opus|fable)-[5-9]", model))


def _shapes(model, max_tokens, temperature, think):
    """Request variants to try in order. A ValidationException (e.g. a field this model/endpoint doesn't
    accept) moves on to the next, simpler variant; the one that works is remembered."""
    plain = ({"maxTokens": max_tokens, "temperature": temperature}, None)
    if not _adaptive(model):
        return [plain]
    off = {"thinking": {"type": "disabled"}}
    if think and PLANNER_EFFORT != "off":
        deep = {"maxTokens": max(max_tokens, 16000)}          # thinking tokens count towards maxTokens
        return [(deep, {"thinking": {"type": "adaptive"}, "output_config": {"effort": PLANNER_EFFORT}}),
                (deep, {"thinking": {"type": "adaptive"}}),
                (deep, None),
                ({"maxTokens": max_tokens, "temperature": temperature}, off)]
    return [({"maxTokens": max_tokens, "temperature": temperature}, off),
            ({"maxTokens": max_tokens}, off),
            ({"maxTokens": max(max_tokens, 8000)}, None)]


def _next_in_chain(model):
    for chain in FALLBACK_CHAINS:
        if model in chain and chain.index(model) + 1 < len(chain):
            return chain[chain.index(model) + 1]
    return None


def converse(model, system, messages, usage, max_tokens=3000, temperature=0.2, tools=None, think=False, cache=False):
    """One Bedrock Converse call. think=True gives Claude 5-series models adaptive thinking (planner and
    repair); every other call runs with thinking off, which is faster and cheaper. cache=True adds prompt-cache
    checkpoints (see _request_parts); a model that rejects them is remembered and called without."""
    bedrock, _, _ = _clients()
    asked, model = model, _SWAPPED.get(model, model)
    shapes = _shapes(model, max_tokens, temperature, think)
    i = _SHAPE_OK.get((model, think), 0)
    attempt = 0
    while True:
        cfg, extra = shapes[i]
        sys_blocks, msgs, tool_list = _request_parts(model, system, messages, tools, cache)
        kwargs = {"modelId": model, "system": sys_blocks, "messages": msgs, "inferenceConfig": cfg}
        if extra:
            kwargs["additionalModelRequestFields"] = extra
        if tool_list:
            kwargs["toolConfig"] = {"tools": tool_list}
        try:
            resp = bedrock.converse(**kwargs)
            usage.add(resp)
            _SHAPE_OK[(model, think)] = i
            return resp
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            err = getattr(e, "response", {}).get("Error", {}) if hasattr(e, "response") else {}
            code, msg = err.get("Code", name), str(err.get("Message", e))
            if any(k in code + name for k in ("Throttling", "ServiceUnavailable", "ModelNotReady")) and attempt < 2:
                attempt += 1
                time.sleep(2 * attempt)
                continue
            model_problem = (code in ("AccessDeniedException", "ResourceNotFoundException")
                             or (code == "ValidationException" and re.search(
                                 r"model identifier|model id|not supported for this model|access", msg, re.I)
                                 and not re.search(r"thinking|effort|output_config|temperature", msg, re.I)))
            if model_problem:
                nxt = _next_in_chain(model)
                if nxt:
                    print(f"model {model} unavailable ({code}: {msg[:160]}); switching to {nxt}")
                    _SWAPPED[asked] = model = nxt
                    shapes, i, attempt = _shapes(model, max_tokens, temperature, think), 0, 0
                    continue
                raise
            if code == "ValidationException" and cache and model not in _NO_CACHE and \
                    re.search(r"cache", msg, re.I):
                print(f"{model}: prompt caching rejected ({msg[:160]}); calling without it")
                _NO_CACHE.add(model)
                continue
            if code == "ValidationException" and i + 1 < len(shapes):
                print(f"{model}: request shape {i} rejected ({msg[:160]}); trying a simpler one")
                i += 1
                continue
            raise


def reply_text(resp):
    return "".join(b.get("text", "") for b in resp["output"]["message"]["content"] if "text" in b)


def ask_json(model, system, user_text, usage, max_tokens=3000, temperature=0.2, think=False, cache=False):
    """One model call that must return JSON; retries once with a reminder if parsing fails."""
    msgs = [{"role": "user", "content": [{"text": user_text}]}]
    resp = converse(model, system, msgs, usage, max_tokens, temperature, think=think, cache=cache)
    text = reply_text(resp)
    try:
        return extract_json(text)
    except (ValueError, json.JSONDecodeError):
        msgs += [{"role": "assistant", "content": [{"text": text or "(empty)"}]},
                 {"role": "user", "content": [{"text": "That was not valid JSON. Reply with ONLY the JSON object."}]}]
        return extract_json(reply_text(converse(model, system, msgs, usage, max_tokens, 0.0, think=think, cache=cache)))


# ====================================================================== airports
# Main airport for popular destinations (cities and countries). Anything else is asked from the small model once.
AIRPORTS = {
    "tokyo": "HND", "japan": "HND", "osaka": "KIX", "kyoto": "KIX", "nara": "KIX", "kobe": "KIX", "sapporo": "CTS",
    "hokkaido": "CTS", "fukuoka": "FUK", "okinawa": "OKA", "naha": "OKA", "nagoya": "NGO", "hiroshima": "HIJ",
    "seoul": "ICN", "korea": "ICN", "south korea": "ICN", "busan": "PUS", "jeju": "CJU",
    "taipei": "TPE", "taiwan": "TPE", "hong kong": "HKG", "macau": "MFM", "shanghai": "PVG", "beijing": "PEK",
    "china": "PVG", "chengdu": "TFU", "guangzhou": "CAN", "shenzhen": "SZX",
    "bangkok": "BKK", "thailand": "BKK", "phuket": "HKT", "chiang mai": "CNX", "krabi": "KBV", "koh samui": "USM",
    "bali": "DPS", "indonesia": "CGK", "jakarta": "CGK", "yogyakarta": "YIA", "lombok": "LOP",
    "kuala lumpur": "KUL", "malaysia": "KUL", "penang": "PEN", "langkawi": "LGK", "kota kinabalu": "BKI",
    "hanoi": "HAN", "vietnam": "SGN", "ho chi minh city": "SGN", "saigon": "SGN", "da nang": "DAD", "hoi an": "DAD",
    "manila": "MNL", "philippines": "MNL", "cebu": "CEB", "boracay": "MPH", "palawan": "PPS",
    "phnom penh": "PNH", "siem reap": "SAI", "cambodia": "PNH", "luang prabang": "LPQ", "yangon": "RGN",
    "singapore": "SIN", "colombo": "CMB", "sri lanka": "CMB", "maldives": "MLE", "male": "MLE",
    "kathmandu": "KTM", "nepal": "KTM", "delhi": "DEL", "new delhi": "DEL", "mumbai": "BOM", "india": "DEL",
    "goa": "GOI", "bangalore": "BLR", "dubai": "DXB", "abu dhabi": "AUH", "doha": "DOH", "istanbul": "IST",
    "turkey": "IST", "cappadocia": "NAV",
    "sydney": "SYD", "australia": "SYD", "melbourne": "MEL", "brisbane": "BNE", "perth": "PER", "cairns": "CNS",
    "gold coast": "OOL", "auckland": "AKL", "new zealand": "AKL", "queenstown": "ZQN", "christchurch": "CHC",
    "london": "LHR", "uk": "LHR", "united kingdom": "LHR", "england": "LHR", "edinburgh": "EDI", "scotland": "EDI",
    "dublin": "DUB", "ireland": "DUB", "paris": "CDG", "france": "CDG", "nice": "NCE", "lyon": "LYS",
    "amsterdam": "AMS", "netherlands": "AMS", "brussels": "BRU", "belgium": "BRU",
    "berlin": "BER", "munich": "MUC", "frankfurt": "FRA", "germany": "FRA", "zurich": "ZRH", "geneva": "GVA",
    "switzerland": "ZRH", "interlaken": "ZRH", "lucerne": "ZRH", "vienna": "VIE", "austria": "VIE", "salzburg": "SZG",
    "prague": "PRG", "czechia": "PRG", "budapest": "BUD", "hungary": "BUD", "warsaw": "WAW", "krakow": "KRK",
    "rome": "FCO", "italy": "FCO", "milan": "MXP", "venice": "VCE", "florence": "FLR", "naples": "NAP",
    "madrid": "MAD", "spain": "MAD", "barcelona": "BCN", "seville": "SVQ", "lisbon": "LIS", "portugal": "LIS",
    "porto": "OPO", "athens": "ATH", "greece": "ATH", "santorini": "JTR", "mykonos": "JMK", "croatia": "ZAG",
    "dubrovnik": "DBV", "split": "SPU",
    "copenhagen": "CPH", "denmark": "CPH", "oslo": "OSL", "norway": "OSL", "bergen": "BGO", "tromso": "TOS",
    "tromsø": "TOS", "lofoten": "BOO", "stockholm": "ARN", "sweden": "ARN", "abisko": "KRN", "kiruna": "KRN",
    "helsinki": "HEL", "finland": "HEL", "rovaniemi": "RVN", "lapland": "RVN", "reykjavik": "KEF", "iceland": "KEF",
    "new york": "JFK", "new york city": "JFK", "nyc": "JFK", "los angeles": "LAX", "san francisco": "SFO",
    "las vegas": "LAS", "seattle": "SEA", "chicago": "ORD", "boston": "BOS", "hawaii": "HNL", "honolulu": "HNL",
    "usa": "JFK", "united states": "JFK", "vancouver": "YVR", "toronto": "YYZ", "canada": "YYZ", "banff": "YYC",
    "mexico city": "MEX", "cancun": "CUN", "lima": "LIM", "cusco": "CUZ", "buenos aires": "EZE", "rio de janeiro": "GIG",
    "cape town": "CPT", "johannesburg": "JNB", "marrakech": "RAK", "morocco": "RAK", "cairo": "CAI", "egypt": "CAI",
    "nairobi": "NBO", "mauritius": "MRU", "zanzibar": "ZNZ",
}
_AIRPORT_CACHE = {}


def resolve_airport(place, usage=None):
    """Main IATA airport for a city/area/country, or "" if unknown. Dictionary first, then one cheap model call."""
    key = re.sub(r"\s+", " ", str(place or "").strip().lower())
    if not key:
        return ""
    for k in (key, key.split(",")[0].strip(), key.split(",")[-1].strip()):
        if k in AIRPORTS:
            return AIRPORTS[k]
    if key in _AIRPORT_CACHE:
        return _AIRPORT_CACHE[key]
    code = ""
    if usage is not None:
        try:
            r = ask_json(INTAKE_MODEL, "ROLE: airport lookup. Reply with ONLY JSON {\"iata\": \"XXX\"}: the main "
                                       "international airport serving the place (\"\" if unsure).", str(place)[:80],
                         usage, max_tokens=40, temperature=0.0)
            c = str(r.get("iata") or "").strip().upper()
            code = c if re.fullmatch(r"[A-Z]{3}", c) else ""
        except Exception as e:  # noqa: BLE001
            print("airport lookup failed:", e)
    _AIRPORT_CACHE[key] = code
    return code


# ====================================================================== spec helpers
_PREF_DEFAULTS = {"vibe": "balanced", "audience": "any", "sortBy": "price", "cabin": "economy", "prefer": "cheapest",
                  "avoidOvernight": True}
_PRIVATE_KEYS = {"missing", "question"}


def slim_spec(spec):
    """The trip spec without empty values, defaults and internal fields: what prompts need, in fewer tokens."""
    def clean(v, key=None):
        if isinstance(v, dict):
            out = {k: clean(x, k) for k, x in v.items() if k not in _PRIVATE_KEYS}
            return {k: x for k, x in out.items() if x not in (None, "", [], {}, 0, False) and _PREF_DEFAULTS.get(k) != x}
        if isinstance(v, list):
            return [clean(x) for x in v]
        return v
    return clean(spec)


def _sj(spec):
    return json.dumps(slim_spec(spec), separators=(",", ":"), ensure_ascii=False)


def normalize_special(spec):
    """Clean mustInclude (named places), dayTrips and per-leg hotels from intake/router."""
    must = []
    for m in spec.get("mustInclude") or []:
        if isinstance(m, str) and m.strip():
            must.append({"name": m.strip()[:60], "city": ""})
        elif isinstance(m, dict) and str(m.get("name") or "").strip():
            must.append({"name": str(m["name"]).strip()[:60], "city": str(m.get("city") or "").strip()[:40]})
    seen, spec["mustInclude"] = set(), []
    for m in must:
        if m["name"].lower() not in seen:
            seen.add(m["name"].lower())
            spec["mustInclude"].append(m)
    spec["mustInclude"] = spec["mustInclude"][:8]
    trips = []
    for t in spec.get("dayTrips") or []:
        t = {"place": t} if isinstance(t, str) else t if isinstance(t, dict) else {}
        place = str(t.get("place") or "").strip()[:40]
        if place and place.lower() not in [x["place"].lower() for x in trips]:
            dt = str(t.get("date") or "")
            trips.append({"place": place, "date": dt if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dt) else "",
                          "fromCity": str(t.get("fromCity") or "").strip()[:40],
                          **{k: t[k] for k in ("lat", "lng") if isinstance(t.get(k), (int, float))}})
    spec["dayTrips"] = trips[:4]
    return spec


def _hotel_wish(spec, leg=None):
    """(name, booked, bookedPriceSGD) of a hotel the traveller named, for the whole trip or one leg."""
    h = {}
    if leg is not None and isinstance(leg.get("hotel"), dict):
        h = leg["hotel"]
    elif leg is None:
        h = ((spec.get("preferences") or {}).get("hotel") or {})
    name = str(h.get("name") or "").strip()[:80]
    price = h.get("bookedPriceSGD")
    price = float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0 else 0.0
    return name, bool(h.get("booked")) and bool(name), price


# ====================================================================== 1 intake
def intake(request, previous, usage, carried=False):
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    system = get_prompt("INTAKE").format(today=today, home_airport=HOME_AIRPORT)
    if carried:
        text = (f"Details of the trip planned just before (JSON): {_sj(previous)}\nThe traveller now wants a different "
                f"trip: {request}\nKeep the dates, travellers, budget, origin and preferences above unless this message "
                f"changes them; destination, places, day trips and hotel come only from this message.")
    else:
        text = request if not previous else (
            f"Earlier details (JSON): {_sj(previous)}\nThe traveller now adds: {request}")
    spec = ask_json(INTAKE_MODEL, system, text, usage, max_tokens=2500, temperature=0.0)
    problems = list(spec.get("missing") or [])
    try:
        s, e = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
        if e < s:
            problems.append("endDate")
        if s < date.today():
            problems.append("startDate")
        if (e - s).days > 20:
            problems.append("trip length (max 21 days)")
    except (KeyError, TypeError, ValueError):
        problems += [f for f in ("startDate", "endDate") if f not in problems]
    if not spec.get("destination"):
        problems.append("destination")
    if not spec.get("budgetSGD"):
        problems.append("budgetSGD")
    spec["pax"] = int(spec.get("pax") or 0) or None
    if not spec["pax"]:
        problems.append("pax")
    spec["missing"] = sorted(set(problems))
    spec.setdefault("interests", ["attraction", "museum", "restaurant"])
    spec.setdefault("pace", "moderate")
    normalize_prefs(spec)
    normalize_special(spec)
    spec["originAirport"] = (spec.get("originAirport") or HOME_AIRPORT).upper()
    spec["destinationAirport"] = (spec.get("destinationAirport") or "").upper()
    if spec.get("destination") and not re.fullmatch(r"[A-Z]{3}", spec["destinationAirport"]):
        spec["destinationAirport"] = resolve_airport(spec["destination"], usage)
    for leg in spec.get("legs") or []:
        if isinstance(leg, dict) and leg.get("city") and not re.fullmatch(r"[A-Z]{3}", str(leg.get("airport") or "").upper()):
            leg["airport"] = resolve_airport(leg["city"], usage)
    if not spec["missing"] and not spec["destinationAirport"] and not (spec.get("legs") or []):
        spec["missing"] = ["destinationAirport"]
        spec["question"] = f"Which airport should I fly you into for {spec['destination']}?"
    if not spec["missing"]:
        problems = normalize_legs(spec)
        if problems:
            spec["missing"] = problems
            spec["question"] = ("Quick check on the route: " + "; ".join(problems) + ". Could you clarify?")
    return spec


MAX_LEGS = 5


def normalize_legs(spec):
    """Multi-city: clean the legs list, make nights add up, and give each leg check-in/check-out dates.
    A single destination ends up with legs = []. Returns a list of problems (empty if fine)."""
    raw = [l for l in (spec.get("legs") or []) if isinstance(l, dict) and str(l.get("city", "")).strip()]
    if len(raw) <= 1:
        spec["legs"] = []
        return []
    if len(raw) > MAX_LEGS:
        return [f"I can plan up to {MAX_LEGS} cities in one trip (you listed {len(raw)})"]
    s, e = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
    total = (e - s).days
    if total < len(raw):
        return [f"{len(raw)} cities need at least {len(raw)} nights, but the trip has {total}"]
    nights = [max(int(l.get("nights") or 0), 0) for l in raw]
    if sum(nights) != total or min(nights) < 1:
        base = [n if n > 0 else 1 for n in nights]
        scaled = [max(1, round(n * total / sum(base))) for n in base]
        while sum(scaled) > total:   # trim from the longest
            scaled[scaled.index(max(scaled))] -= 1
        while sum(scaled) < total:
            scaled[scaled.index(min(scaled))] += 1
        nights = scaled
    legs, d = [], s
    for l, n in zip(raw, nights):
        airport = str(l.get("airport") or "").upper()
        legs.append({"city": str(l["city"]).strip(), "airport": airport if re.fullmatch(r"[A-Z]{3}", airport) else "",
                     "nights": n, "checkIn": d.isoformat(), "checkOut": (d + timedelta(days=n)).isoformat()})
        if isinstance(l.get("hotel"), dict) and str(l["hotel"].get("name") or "").strip():
            legs[-1]["hotel"] = {k: l["hotel"][k] for k in ("name", "booked", "bookedPriceSGD") if k in l["hotel"]}
        d += timedelta(days=n)
    problems = []
    for which, leg in (("first", legs[0]), ("last", legs[-1])):
        if not leg["airport"]:
            problems.append(f"which airport to fly {'into' if which == 'first' else 'home from'} {leg['city']}")
    spec["legs"] = legs
    spec["destination"] = " → ".join(l["city"] for l in legs)
    spec["destinationAirport"] = legs[0]["airport"]
    return problems


def is_multi(spec):
    return len(spec.get("legs") or []) > 1


def leg_index(spec, dt):
    """Index of the leg you sleep in / start from on date dt (moving days count as the new city)."""
    idx = 0
    for i, l in enumerate(spec["legs"]):
        if l["checkIn"] <= dt:
            idx = i
    return idx


def trip_spec_for_tools(spec):
    max_stops = {"relaxed": 4, "moderate": 5, "packed": 7}.get(spec.get("pace"), 5)
    min_free = {"relaxed": 180, "moderate": 90, "packed": 0}.get(spec.get("pace"), 60)
    p = spec.get("preferences") or {}
    return {"startDate": spec["startDate"], "endDate": spec["endDate"], "pax": spec["pax"],
            "budgetSGD": float(spec["budgetSGD"]), "dayStart": p.get("dayStart") or "09:00",
            "dayEnd": p.get("dayEnd") or "21:00",
            "hardConstraints": {"maxStopsPerDay": max_stops, "minFreeMinutesPerDay": min_free},
            "mustVisit": [x["placeId"] for x in spec.get("pinned") or [] if x.get("placeId")]}


# ====================================================================== personal preferences -> tool parameters
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def normalize_prefs(spec):
    """Clean the 'preferences' object from intake/router so every tool gets valid values."""
    p = spec.get("preferences") if isinstance(spec.get("preferences"), dict) else {}
    lst = lambda v: [str(x).strip() for x in (v if isinstance(v, list) else [v] if v else []) if str(x).strip()]
    hhmm = lambda v: v if isinstance(v, str) and _HHMM.match(v.strip()) else ""
    hotel = p.get("hotel") if isinstance(p.get("hotel"), dict) else {}
    flight = p.get("flight") if isinstance(p.get("flight"), dict) else {}
    transfer = p.get("transfer") if isinstance(p.get("transfer"), dict) else {}
    num = lambda v, lo, hi: min(max(float(v), lo), hi) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
    stops = flight.get("maxStops")
    out = {
        "vibe": p.get("vibe") if p.get("vibe") in ("classic", "balanced", "hidden_gems") else "balanced",
        "dietary": [d.lower().replace(" ", "_").replace("-", "_") for d in lst(p.get("dietary"))],
        "accessibility": ["wheelchair"] if lst(p.get("accessibility")) else [],
        "audience": p.get("audience") if p.get("audience") in ("family", "adults") else "any",
        "indoorPreferred": bool(p.get("indoorPreferred")), "freeOnly": bool(p.get("freeOnly")),
        "avoid": lst(p.get("avoid")) + [a for a in lst(spec.get("avoid")) if len(a) < 30],
        "dayStart": hhmm(p.get("dayStart")), "dayEnd": hhmm(p.get("dayEnd")),
        "hotel": {"style": [x.lower() for x in lst(hotel.get("style"))], "minStars": num(hotel.get("minStars"), 0, 5),
                  "minReviewScore": num(hotel.get("minReviewScore"), 0, 10), "area": str(hotel.get("area") or "")[:60],
                  "sortBy": hotel.get("sortBy") if hotel.get("sortBy") in ("price", "rating", "value") else "price",
                  "name": str(hotel.get("name") or "").strip()[:80], "booked": bool(hotel.get("booked")),
                  "bookedPriceSGD": num(hotel.get("bookedPriceSGD"), 0, 1e6)},
        "flight": {"maxStops": int(stops) if isinstance(stops, (int, float)) and not isinstance(stops, bool) else None,
                   "departAfter": hhmm(flight.get("departAfter")), "departBefore": hhmm(flight.get("departBefore")),
                   "returnDepartAfter": hhmm(flight.get("returnDepartAfter")),
                   "returnDepartBefore": hhmm(flight.get("returnDepartBefore")),
                   "preferredAirlines": lst(flight.get("preferredAirlines")),
                   "avoidAirlines": lst(flight.get("avoidAirlines")),
                   "sortBy": flight.get("sortBy") if flight.get("sortBy") in ("price", "duration", "best") else "price",
                   "cabin": flight.get("cabin") if flight.get("cabin") in ("economy", "premium_economy", "business",
                                                                           "first") else "economy"},
        "transfer": {"prefer": transfer.get("prefer") if transfer.get("prefer") in ("cheapest", "fastest", "ground",
                                                                                    "flight") else "cheapest",
                     "avoidOvernight": transfer.get("avoidOvernight") is not False},
        "notes": str(p.get("notes") or "")[:300],
    }
    out["avoid"] = list(dict.fromkeys(out["avoid"]))[:8]
    spec["preferences"] = out
    return out


def pref_args(tool, spec):
    """The traveller's preferences as parameters of one tool (see the tool schemas)."""
    p = spec.get("preferences") or normalize_prefs(spec)
    if tool == "find_places":
        a = {"vibe": p["vibe"], "dietary": p["dietary"], "accessibility": p["accessibility"], "audience": p["audience"],
             "indoorOnly": p["indoorPreferred"], "freeOnly": p["freeOnly"], "avoid": p["avoid"]}
        if spec.get("cuisine"):
            a["cuisine"] = spec["cuisine"]
    elif tool == "search_stays":
        h = p["hotel"]
        a = {"style": h["style"], "minStars": h["minStars"], "minReviewScore": h["minReviewScore"], "sortBy": h["sortBy"]}
    elif tool == "search_flights":
        f = p["flight"]
        a = {k: f[k] for k in ("maxStops", "departAfter", "departBefore", "returnDepartAfter", "returnDepartBefore",
                               "preferredAirlines", "avoidAirlines", "sortBy", "cabin")}
    elif tool == "search_transfers":
        a = dict(p["transfer"])
    else:
        return {}
    return {k: v for k, v in a.items() if v not in (None, "", [], 0, False, "any", "balanced", "price", "cheapest",
                                                     "economy") or (k == "avoidOvernight" and v is False)}


def with_prefs(tool, spec, args):
    """Tool arguments with the traveller's preferences filled in. Values the model chose itself win, so the model
    can still refine them; preferences it forgot are added, so no personal wish is silently dropped."""
    out = {**pref_args(tool, spec), **(args or {})}
    if tool == "search_stays" and not is_multi(spec) and "name" not in (args or {}):
        name, booked, price = _hotel_wish(spec)
        if name:
            out.update(name=name, alreadyBooked=booked, **({"bookedPriceSGD": price} if price else {}))
    area = ((spec.get("preferences") or {}).get("hotel") or {}).get("area")
    if tool == "search_stays" and area and out.get("lat") is None and "," not in str(out.get("city", "")):
        out["city"] = f'{area}, {out.get("city") or spec.get("destination")}'
    return out


# ====================================================================== 2 gather (model chooses tools)
def _slim_result(short, res, n=8):
    """What the gather model needs from a tool result, in as few tokens as possible: IDs, names, prices and the
    preference matches. Full results still go into the candidate pool."""
    if not res.get("ok"):
        return {k: res.get(k) for k in ("ok", "error", "message") if res.get(k) is not None}
    out = {"ok": True, "source": res.get("source")}
    if short == "search_flights":
        out["offers"] = [_c_flight(f) for f in (res.get("offers") or [])[:n]]
    elif short == "search_stays":
        out["stays"] = [_c_stay(x) for x in (res.get("stays") or [])[:n]]
    elif short == "find_places":
        out["places"] = [{k: v for k, v in _c_place(p, with_hours=False).items() if k != "about"}
                         for p in (res.get("places") or [])[:n * 3]]
    for k in ("note", "notFound"):
        if res.get(k):
            out[k] = res[k]
    return out


def gather(spec, usage, metrics):
    _, _, gw = _clients()
    specs = []
    for t in gw.tools():
        short = t["name"].split("___")[-1]
        if short in GATHER_TOOLS:
            specs.append({"toolSpec": {"name": t["name"], "description": t.get("description", short),
                                       "inputSchema": {"json": t.get("inputSchema", {"type": "object"})}}})
    pool = {"flights": [], "stays": [], "places": [], "transfers": []}
    sources = set()
    calls = []
    system = get_prompt("GATHER").format(spec=_sj({k: v for k, v in spec.items()
                                                    if k not in ("mustInclude", "dayTrips", "rememberedPreferences")}))
    messages = [{"role": "user", "content": [{"text": "Gather the options for this trip now."}]}]

    def absorb(short, res):
        if not res.get("ok"):
            return
        sources.add(f"{short}:{res.get('source')}")
        if short == "search_flights":
            pool["flights"] += res.get("offers", [])
        elif short == "search_stays":
            pool["stays"] += res.get("stays", [])
        elif short == "find_places":
            have = {p["placeId"] for p in pool["places"]}
            pool["places"] += [p for p in res.get("places", []) if p["placeId"] not in have]

    for _ in range(6):   # at most 6 model turns
        resp = converse(PLANNER_MODEL, system, messages, usage, max_tokens=1500, temperature=0.0, tools=specs,
                        cache=True)
        msg = resp["output"]["message"]
        messages.append(msg)
        uses = [b["toolUse"] for b in msg["content"] if "toolUse" in b]
        if not uses:
            break
        results = []
        for u in uses:
            short = u["name"].split("___")[-1]
            calls.append(short)
            yield ev("status", {"search_flights": "Searching flights…", "search_stays": "Searching hotels…",
                                "find_places": "Finding attractions and restaurants…"}.get(short, f"Calling {short}…"))
            try:
                res = gw.call(u["name"], with_prefs(short, spec, u.get("input", {})))
            except GatewayError as e:
                res = {"ok": False, "error": "gateway_error", "message": str(e)}
            absorb(short, res)
            slim = json.dumps(_slim_result(short, res), separators=(",", ":"), ensure_ascii=False)
            results.append({"toolResult": {"toolUseId": u["toolUseId"],
                                           "content": [{"text": slim[:8000]}],
                                           "status": "success" if res.get("ok") else "error"}})
        messages.append({"role": "user", "content": results})

    # Safety net: if the model skipped a required search, run it ourselves (counted in metrics).
    fallback = []
    if not pool["flights"]:
        fallback.append(("search_flights", {"origin": spec["originAirport"], "destination": spec["destinationAirport"],
                                            "departDate": spec["startDate"], "returnDate": spec["endDate"],
                                            "pax": spec["pax"]}))
    if not pool["stays"]:
        fallback.append(("search_stays", {"city": spec["destination"], "checkIn": spec["startDate"],
                                          "checkOut": spec["endDate"], "guests": spec["pax"]}))
    if not pool["places"]:
        cats = [c for c in spec.get("interests", []) if c in PLACE_CATEGORIES] or ["attraction", "museum"]
        kws = [c for c in spec.get("interests", []) if c and c not in PLACE_CATEGORIES] + list(spec.get("activities") or [])
        fallback.append(("find_places", {"city": spec["destination"], "categories": sorted(set(cats + ["restaurant"])),
                                         "keywords": kws[:5]}))
    for short, args in fallback:
        yield ev("status", f"Double-checking: {short.replace('_', ' ')}…")
        try:
            absorb(short, gw.call(short, with_prefs(short, spec, args)))
        except GatewayError as e:
            print("fallback call failed:", e)
    yield from gather_specials(spec, pool, calls, sources)
    metrics.update(toolCalls=calls, fallbackCalls=[f[0] for f in fallback], dataSources=sorted(sources))
    return pool


def _merge_places(pool, places):
    """Add places to the pool; a place found again replaces the old copy (it may now be 'requested')."""
    fresh = {p["placeId"]: p for p in places}
    pool["places"] = [p for p in places] + [p for p in pool["places"] if p["placeId"] not in fresh]


def _km(a, b):
    import math
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def gather_specials(spec, pool, calls, sources, only=None):
    """Code-driven lookups for things the traveller named: specific places (pinned, must be in the plan) and
    day-trip towns (their own places, tagged dayTrip). Works for single- and multi-city trips.
    only: subset of {"places", "dayTrips"} to refresh (default both)."""
    _, _, gw = _clients()
    multi = is_multi(spec)
    cities = [l["city"] for l in spec["legs"]] if multi else [spec["destination"]]

    def call(args):
        calls.append("find_places")
        try:
            r = gw.call("find_places", with_prefs("find_places", spec, args))
        except GatewayError as e:
            r = {"ok": False, "message": str(e)}
        if r.get("ok"):
            sources.add(f"find_places:{r.get('source')}")
        return r

    def leg_of(city):
        c = (city or "").strip().lower()
        for i, name in enumerate(cities):
            if c and (c == name.lower() or c in name.lower() or name.lower() in c):
                return i
        return None

    if spec.get("mustInclude") and (only is None or "places" in only):
        yield ev("status", "Looking up the places you mentioned: " + ", ".join(m["name"] for m in spec["mustInclude"]) + "…")
        hits = {}
        groups = {}
        for m in spec["mustInclude"]:
            li = leg_of(m.get("city")) if multi else 0
            for i in ([li] if li is not None else range(len(cities))):
                groups.setdefault(i, []).append(m["name"])
        for i, names in groups.items():
            r = call({"city": cities[i], "names": names})
            for p_ in r.get("places", []) if r.get("ok") else []:
                if p_.get("requested") and (p_["requested"] not in hits
                                            or p_.get("distanceKm", 0) < hits[p_["requested"]][0].get("distanceKm", 0)):
                    hits[p_["requested"]] = (p_, i)
        found = []
        for name, (p_, i) in hits.items():
            found.append({**p_, **({"city": cities[i], "leg": i} if multi else {})})
        _merge_places(pool, found)
        spec["pinned"] = [{"name": p_["requested"], "placeId": p_["placeId"], **({"city": p_["city"]} if multi else {})}
                          for p_ in found]
        spec["notFound"] = [m["name"] for m in spec["mustInclude"] if m["name"] not in hits]
    if spec.get("dayTrips") and (only is None or "dayTrips" in only):
        cats = sorted({c for c in spec.get("interests", []) if c in PLACE_CATEGORIES and c not in OUTDOOR}
                      - {"restaurant"})[:3] or ["attraction"]
        for t in spec["dayTrips"]:
            yield ev("status", f"Finding things to do on a day trip to {t['place']}…")
            r = call({"city": t["place"], "categories": cats + ["restaurant"], "radiusKm": 5, "maxPerCategory": 4})
            if not r.get("ok") or not r.get("places"):
                t["notFound"] = True
                continue
            q = r.get("query") or {}
            if q.get("lat") is not None:
                t.update(lat=q["lat"], lng=q["lng"])
            li = leg_of(t.get("fromCity")) if multi else None
            if multi and li is None:   # nearest city of the route
                centres = [(i, pl) for i, pl in ((p_.get("leg"), p_) for p_ in pool["places"]) if i is not None]
                if centres and t.get("lat") is not None:
                    li = min(centres, key=lambda c: _km(c[1], t))[0]
            tag = {"dayTrip": t["place"], **({"city": cities[li], "leg": li} if multi and li is not None else {})}
            if multi and li is not None:
                t["fromCity"] = cities[li]
            _merge_places(pool, [{**p_, **tag} for p_ in r["places"]])


def _c_flight(f):
    item = {"id": f["offerId"], "airline": f.get("airline"), "priceSGD": f.get("priceTotalSGD"),
            "legs": [f"{s.get('from')}->{s.get('to')} dep {s.get('departAt')} arr {s.get('arriveAt')} "
                     f"stops {s.get('stops', 0)}" for s in f.get("slices", [])]}
    for k in ("matches", "filterNote"):
        if f.get(k):
            item[k] = f[k]
    return item


def _c_stay(s_):
    item = {"id": s_["stayId"], "name": s_.get("name"), "stars": s_.get("starRating"), "review": s_.get("reviewScore"),
            "priceSGD": s_.get("priceTotalSGD"), "perNightSGD": s_.get("pricePerNightSGD"), "style": s_.get("style")}
    for k in ("matches", "filterNote", "booked"):
        if s_.get(k):
            item[k] = s_[k]
    return item


def _c_place(p, with_hours=True):
    item = {"id": p["placeId"], "name": p.get("name"), "category": p.get("category"), "distanceKm": p.get("distanceKm")}
    for k in ("requested", "dayTrip"):
        if p.get(k):
            item[k] = p[k]
    if with_hours:
        item["hours"] = p.get("openSimple")
    tags = [t for t in (p.get("tags") or []) if t not in ("indoor", "requested by user")][:5]
    if tags:
        item["tags"] = tags
    for k in ("activity", "matches"):
        if p.get(k):
            item[k] = p[k]
    if p.get("description"):
        item["about"] = p["description"][:90]
    return item


def _order_places(places, n=None):
    """Named places and day-trip places are always included; then places that meet the traveller's preferences,
    and requested activities, first."""
    must = [p for p in places if p.get("requested") or p.get("dayTrip")]
    rest = sorted((p for p in places if not (p.get("requested") or p.get("dayTrip"))),
                  key=lambda p: (-len(p.get("matches") or []),
                                 -int(bool(p.get("activity")) or p.get("category") in OUTDOOR)))
    return must + (rest if n is None else rest[:max(n - len(must), 0)])


def compact_pool(pool):
    flights = [_c_flight(f) for f in pool["flights"][:6]]
    stays = [_c_stay(s_) for s_ in pool["stays"][:8]]
    places = [_c_place(p) for p in _order_places(pool["places"], 60)]
    return json.dumps({"flights": flights, "stays": stays, "places": places}, separators=(",", ":"))


def gather_multi(spec, usage, metrics):
    """Multi-city search, done in code (many calls with fixed arguments): an open-jaw international flight,
    hotels and places per city, and transfers per hop. Results are tagged with their city/leg."""
    _, _, gw = _clients()
    legs, pax = spec["legs"], spec["pax"]
    pool = {"flights": [], "stays": [], "places": [], "transfers": []}
    sources, calls = set(), []

    def call(tool, args):
        calls.append(tool)
        try:
            res = gw.call(tool, with_prefs(tool, spec, args))
        except GatewayError as e:
            res = {"ok": False, "error": "gateway_error", "message": str(e)}
        if res.get("ok"):
            sources.add(f"{tool}:{res.get('source')}")
        else:
            print(f"{tool} failed:", res)
        return res

    yield ev("status", f"Searching flights {spec['originAirport']} → {legs[0]['city']}, home from {legs[-1]['city']}…")
    res = call("search_flights", {"origin": spec["originAirport"], "destination": legs[0]["airport"],
                                  "departDate": spec["startDate"], "returnDate": spec["endDate"],
                                  "returnFrom": legs[-1]["airport"], "pax": pax})
    pool["flights"] = res.get("offers", []) if res.get("ok") else []

    cats = sorted({c for c in spec.get("interests", []) if c in PLACE_CATEGORIES} | {"restaurant"})
    kws = ([c for c in spec.get("interests", []) if c and c not in PLACE_CATEGORIES]
           + list(spec.get("activities") or []))[:5]
    for i, leg in enumerate(legs):
        yield ev("status", f"Searching hotels and places in {leg['city']} ({leg['nights']} nights)…")
        name, booked, price = _hotel_wish(spec, leg)
        extra = {"name": name, "alreadyBooked": booked, **({"bookedPriceSGD": price} if price else {})} if name else {}
        res = call("search_stays", {"city": leg["city"], "checkIn": leg["checkIn"], "checkOut": leg["checkOut"],
                                    "guests": pax, **extra})
        for st in res.get("stays", []) if res.get("ok") else []:
            pool["stays"].append({**st, "city": leg["city"], "leg": i})
        res = call("find_places", {"city": leg["city"], "categories": cats, "keywords": kws, "radiusKm": 6,
                                   "maxPerCategory": 6})
        for pl in res.get("places", []) if res.get("ok") else []:
            pool["places"].append({**pl, "city": leg["city"], "leg": i})
    for i, (a, b) in enumerate(zip(legs, legs[1:])):
        yield ev("status", f"Finding ways to get from {a['city']} to {b['city']} on {a['checkOut']}…")
        res = call("search_transfers", {"from": {"city": a["city"], "airport": a["airport"]},
                                        "to": {"city": b["city"], "airport": b["airport"]},
                                        "date": a["checkOut"], "pax": pax})
        for o in res.get("options", []) if res.get("ok") else []:
            pool["transfers"].append({**o, "hop": i})
    yield from gather_specials(spec, pool, calls, sources)
    metrics.update(toolCalls=calls, fallbackCalls=[], dataSources=sorted(sources), gatherMode="multi-city (code)")
    return pool


def multi_pool_gaps(spec, pool):
    gaps = []
    if not pool["flights"]:
        gaps.append("international flights")
    for i, leg in enumerate(spec["legs"]):
        if not any(s_.get("leg") == i for s_ in pool["stays"]):
            gaps.append(f"hotels in {leg['city']}")
    for i, (a, b) in enumerate(zip(spec["legs"], spec["legs"][1:])):
        if not any(t.get("hop") == i for t in pool["transfers"]):
            gaps.append(f"a way to get from {a['city']} to {b['city']}")
    return gaps


def compact_pool_multi(spec, pool):
    flights = [_c_flight(f) for f in pool["flights"][:5]]
    cities = []
    for i, leg in enumerate(spec["legs"]):
        stays = [_c_stay(s_) for s_ in pool["stays"] if s_.get("leg") == i][:5]
        places = [_c_place(p) for p in _order_places([p for p in pool["places"] if p.get("leg") == i], 22)]
        cities.append({"city": leg["city"], "nights": leg["nights"], "checkIn": leg["checkIn"],
                       "checkOut": leg["checkOut"], "stays": stays, "places": places})
    hops = []
    for i, (a, b) in enumerate(zip(spec["legs"], spec["legs"][1:])):
        hops.append({"from": a["city"], "to": b["city"], "date": a["checkOut"], "options": [
            {"id": t["transferId"], "mode": t.get("mode"), "summary": t.get("summary"), "depart": t.get("departAt"),
             "arrive": t.get("arriveAt"), "minutes": t.get("durationMinutes"), "priceSGD": t.get("priceTotalSGD"),
             "estimate": t.get("estimate"), "overnight": t.get("overnight", False), "matches": t.get("matches") or []}
            for t in pool["transfers"] if t.get("hop") == i]})
    return json.dumps({"internationalFlights": flights, "cities": cities, "transfers": hops}, separators=(",", ":"))


# ====================================================================== 3/7 propose + repair
def _bookable(spec):
    s, e = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
    return round(float(spec["budgetSGD"]) - DAILY_SPEND * spec["pax"] * ((e - s).days + 1))


def planning_context(spec, pool):
    """Trip + candidate pool as one text block. It is identical for propose and every repair round of the same
    trip, so it is sent as a cached system block (see _request_parts) and paid for in full only once."""
    multi = is_multi(spec)
    return get_prompt("POOL_CONTEXT_MULTI" if multi else "POOL_CONTEXT").format(
        spec=_sj(spec), daily=f"{DAILY_SPEND:g}", bookable=_bookable(spec),
        pool=compact_pool_multi(spec, pool) if multi else compact_pool(pool))


def _special_rules(spec):
    """Extra planner rules, only when the trip needs them (pinned places, a named hotel, day trips)."""
    lines = get_prompt("SPECIAL_RULES").split("\n- ")
    want = [bool(spec.get("pinned")), bool(_hotel_wish(spec)[0] or any(l.get("hotel") for l in spec.get("legs") or [])),
            any(t.get("lat") is not None for t in spec.get("dayTrips") or [])]
    keep = [ln for ln, w in zip(lines, want) if w]
    return ("- " + "\n- ".join(x.lstrip("- ") for x in keep)) if keep else ""


def propose(spec, pool, usage):
    multi = is_multi(spec)
    role = get_prompt("PLANNER_MULTI" if multi else "PLANNER").format(special=_special_rules(spec))
    return ask_json(PLANNER_MODEL, [planning_context(spec, pool), role], "Create the plan.", usage, max_tokens=3000,
                    temperature=0.3, think=True, cache=True)


def repair(spec, pool, plan, verify_res, judge_res, unscheduled, usage, change=None):
    violations = [{k: v.get(k) for k in ("check", "message", "repairHint", "day", "item") if v.get(k)}
                  for v in (verify_res or {}).get("violations", [])]
    for u in unscheduled:
        violations.append({"check": "did_not_fit", "message": f"{u['name']} did not fit on {u['date']}",
                           "item": u["placeId"]})
    critique = "none" if not judge_res else json.dumps({"scores": judge_res.get("scores"),
                                                        "suggestions": judge_res.get("suggestions", [])})
    multi = is_multi(spec)
    role = get_prompt("REPAIR_MULTI" if multi else "REPAIR").format(
        plan=json.dumps(plan, separators=(",", ":")), change=change or "none", critique=critique,
        violations=json.dumps(violations, separators=(",", ":")) if violations else "none",
        special=_special_rules(spec))
    return ask_json(PLANNER_MODEL, [planning_context(spec, pool), role], "Return the repaired plan.", usage,
                    max_tokens=3000, temperature=0.2, think=True, cache=True)


# ====================================================================== 4 sequence (code)
def _flight_window(spec, pool, plan):
    """(arrival floor on day 1, departure ceiling on the last day) in minutes, from the international flight."""
    flight = next((f for f in pool["flights"] if f["offerId"] == plan.get("flightOfferId")), None)
    arrive_floor = depart_ceiling = None
    if flight and flight.get("slices"):
        arr = flight["slices"][0].get("arriveAt") or ""
        if arr[:10] == spec["startDate"]:
            arrive_floor = _hm(arr[11:16]) + ARRIVAL_BUFFER_MIN
        if len(flight["slices"]) > 1:
            dep = flight["slices"][-1].get("departAt") or ""
            if dep[:10] == spec["endDate"]:
                depart_ceiling = _hm(dep[11:16]) - DEPARTURE_BUFFER_MIN
    return arrive_floor, depart_ceiling


def _plan_stays(spec, pool, plan):
    """Multi-city: match the plan's stays/transfers to the legs (by city name, else by order)."""
    legs = spec["legs"]
    by_city = {str(x.get("city", "")).strip().lower(): x.get("stayId") for x in plan.get("stays") or []}
    listed = [x.get("stayId") for x in plan.get("stays") or []]
    stays = []
    for i, leg in enumerate(legs):
        sid = by_city.get(leg["city"].lower()) or (listed[i] if i < len(listed) else None)
        if not sid:   # planner forgot this city: take the cheapest option so the verifier can still price it
            opts = sorted((s_ for s_ in pool["stays"] if s_.get("leg") == i),
                          key=lambda s_: s_.get("priceTotalSGD") or 1e9)
            sid = opts[0]["stayId"] if opts else None
        stays.append({"stayId": sid, "checkIn": leg["checkIn"], "checkOut": leg["checkOut"], "city": leg["city"]})
    tids = [x.get("transferId") for x in plan.get("transfers") or []]
    transfers = []
    for k in range(len(legs) - 1):
        tid = tids[k] if k < len(tids) else None
        if not tid:
            opts = [t for t in pool["transfers"] if t.get("hop") == k and not t.get("overnight")]
            tid = opts[0]["transferId"] if opts else None
        transfers.append({"transferId": tid, "date": legs[k]["checkOut"],
                          "from": legs[k]["city"], "to": legs[k + 1]["city"]})
    return stays, transfers


def sequence(spec, pool, plan):
    """Calls sequence_day for each day, respecting flight arrival/departure times. Multi-city trips use that
    night's hotel as the day's base and start moving days after the transfer arrives."""
    _, _, gw = _clients()
    multi = is_multi(spec)
    arrive_floor, depart_ceiling = _flight_window(spec, pool, plan)
    if multi:
        stays, transfers = _plan_stays(spec, pool, plan)
    else:
        stays = [{"stayId": plan.get("stayId"), "checkIn": spec["startDate"], "checkOut": spec["endDate"],
                  "city": spec.get("destination")}]
        transfers = []
    t_by_id = {t["transferId"]: t for t in pool.get("transfers", [])}
    move_on = {t["date"]: {**t_by_id.get(t["transferId"], {}), **t} for t in transfers if t["transferId"]}
    s, e = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
    by_date = {d.get("date"): d for d in plan.get("days", [])}
    days, unscheduled = [], []
    for i in range((e - s).days + 1):
        dt = (s + timedelta(days=i)).isoformat()
        d = by_date.get(dt, {"date": dt, "placeIds": []})
        li = leg_index(spec, dt) if multi else 0
        stay = stays[min(li, len(stays) - 1)]
        prefs = spec.get("preferences") or {}
        start, end = _hm(prefs.get("dayStart") or "09:00"), _hm(prefs.get("dayEnd") or "21:00")
        if dt == spec["startDate"] and arrive_floor is not None:
            start = max(start, arrive_floor)
        if dt == spec["endDate"] and depart_ceiling is not None:
            end = min(end, depart_ceiling)
        day = {"date": dt, "theme": d.get("theme", ""), "stops": []}
        if multi:
            day["city"] = stay["city"]
        mv = move_on.get(dt)
        if mv:
            arr = mv.get("arriveAt") or ""
            day["transfer"] = {k: mv.get(k) for k in ("transferId", "mode", "summary", "departAt", "arriveAt",
                                                       "durationMinutes", "estimate", "bookingUrl", "from", "to")}
            if mv.get("overnight") or arr[:10] != dt:
                end = start   # arrives late / next day: no sightseeing
            else:
                start = max(start, _hm(arr[11:16]) + TRANSFER_BUFFER_MIN)
        ids = [p for p in d.get("placeIds", []) if isinstance(p, str)]
        trip = _day_trip_for(spec, pool, d, ids, stay)
        seq_args = {"date": dt, "stayId": stay["stayId"]}
        if trip:
            day["dayTrip"] = trip
            start, end = start + trip["travelMinutes"], end - trip["travelMinutes"]
            seq_args = {"date": dt, "start": {"id": "daytrip", "name": trip["place"], "lat": trip["lat"],
                                              "lng": trip["lng"]}}
        if ids and end - start >= 60:
            res = gw.call("sequence_day", {**seq_args, "placeIds": ids, "dayStart": _fmt(start), "dayEnd": _fmt(end),
                                           "mode": "walk", "lunch": True})
            if res.get("ok"):
                day["stops"] = [{k: s_.get(k) for k in ("placeId", "name", "category", "arriveAt", "departAt",
                                                         "travelMinutesFromPrev", "travelBy")}
                                for s_ in res.get("stops", [])]
                unscheduled += [{**u, "date": dt} for u in res.get("unscheduled", [])]
            else:
                # e.g. unknown IDs -> leave times off; the verifier will flag it for repair
                day["stops"] = [{"placeId": p} for p in ids]
        elif ids:
            unscheduled += [{"placeId": p, "name": p, "date": dt, "reason": "no time on this day"} for p in ids]
        days.append(day)
    out = {"flightOfferId": plan.get("flightOfferId"), "stayId": stays[0]["stayId"], "days": days}
    if multi:
        out["stays"] = stays
        out["transfers"] = [{"transferId": t["transferId"], "date": t["date"]} for t in transfers]
    return out, unscheduled


DAY_TRIP_MIN_KM = 25   # closer than this is just another part of the city


def _day_trip_for(spec, pool, d, ids, stay):
    """If this day is a day trip (the planner said so, or most of its places are day-trip places), return
    {place, lat, lng, travelMinutes, travelBy} with the one-way time estimated from the hotel."""
    trips = {t["place"].lower(): t for t in spec.get("dayTrips") or [] if t.get("lat") is not None}
    if not trips:
        return None
    by_id = {p_["placeId"]: p_ for p_ in pool["places"]}
    name = str(d.get("dayTrip") or "").strip().lower()
    if name not in trips:
        tagged = [by_id.get(i, {}).get("dayTrip", "").lower() for i in ids]
        tagged = [x for x in tagged if x in trips]
        name = max(set(tagged), key=tagged.count) if tagged and len(tagged) * 2 >= len(ids) else ""
    if name not in trips:
        return None
    t = trips[name]
    hotel = next((x for x in pool["stays"] if x["stayId"] == stay.get("stayId") and x.get("lat") is not None), None)
    km = _km(hotel, t) if hotel else 60.0
    if km < DAY_TRIP_MIN_KM:
        return None
    minutes = int(round(km / 70 * 60 / 5) * 5) + 25     # regional train/bus ~70 km/h + getting to the station
    return {"place": t["place"], "lat": t["lat"], "lng": t["lng"], "travelMinutes": min(minutes, 240),
            "travelBy": "train or bus", "km": round(km)}


def _stays_of(seq, pool):
    ids = [x["stayId"] for x in seq.get("stays") or []] or [seq.get("stayId")]
    by_id = {s_["stayId"]: s_ for s_ in pool["stays"]}
    return [{**by_id.get(i, {}), **next((x for x in seq.get("stays") or [] if x["stayId"] == i), {})} for i in ids]


def _route_text(spec):
    if not is_multi(spec):
        return ""
    return " → ".join(f"{l['city']} ({l['nights']}n)" for l in spec["legs"])


def _plan_lines(seq, pool, with_prices=False):
    """Readable lines for judge / chat / router: hotels, transfers, then each day."""
    names = {p["placeId"]: p for p in pool["places"]}
    lines = []
    for st in _stays_of(seq, pool):
        where = f" in {st['city']}" if st.get("city") else ""
        dates = f" {st['checkIn']}..{st['checkOut']}" if st.get("checkIn") and seq.get("stays") else ""
        price = f", SGD {st.get('priceTotalSGD')} total" if with_prices else ""
        style = f", {'/'.join(st.get('style') or [])}" if st.get("style") else ""
        booked = ", already booked by the traveller" if st.get("booked") else ""
        lines.append(f"Hotel{where}{dates}: {st.get('name')} ({st.get('starRating')} stars, "
                     f"review {st.get('reviewScore')}{style}{price}{booked})")
    for d in seq["days"]:
        t = d.get("transfer")
        move = ""
        if t:
            move = (f" | MOVE {t.get('summary')} dep {(t.get('departAt') or '')[11:16]} arr "
                    f"{(t.get('arriveAt') or '')[11:16]}" + (" (estimate)" if t.get("estimate") else ""))
        def label(s):
            p = names.get(s["placeId"], {})
            extra = ", ".join((p.get("matches") or []) + [t for t in (p.get("tags") or [])
                                                            if t in ("local pick", "well-known")][:1])
            if p.get("requested"):
                extra = ("requested by traveller, " + extra).rstrip(", ")
            return (f"{s.get('arriveAt', '')} {p.get('name', s['placeId'])} ({p.get('category', '')}"
                    + (f"; {extra}" if extra else "") + ")")
        stops = ", ".join(label(s) for s in d["stops"]) or "free / travel"
        city = f" {d['city']}" if d.get("city") else ""
        if d.get("dayTrip"):
            move += f" | DAY TRIP to {d['dayTrip']['place']} (~{d['dayTrip']['travelMinutes']} min each way)"
        lines.append(f"{d['date']}{city} [{d.get('theme', '')}]{move}: {stops}")
    return lines


# ====================================================================== 6 judge
def judge(request, spec, seq, pool, usage):
    lines = _plan_lines(seq, pool)
    if is_multi(spec):
        lines.insert(0, "Route: " + _route_text(spec) + " (moving days are meant to be light)")
    system = get_prompt("JUDGE").format(request=request[-1500:], spec=_sj(spec), plan="\n".join(lines))
    res = ask_json(JUDGE_MODEL, system, "Score this itinerary.", usage, max_tokens=800, temperature=0.0)
    scores = {k: int(v) for k, v in (res.get("scores") or {}).items() if str(v).strip().isdigit()}
    res["scores"] = scores
    res["minScore"] = min(scores.values()) if scores else 0
    res["passed"] = bool(scores) and res["minScore"] >= JUDGE_PASS
    return res


# ====================================================================== 8 deliver
def narrative(spec, seq, pool, usage):
    names = {p["placeId"]: p for p in pool["places"]}
    stays = _stays_of(seq, pool)
    facts = {"hotel": stays[0].get("name"), "days": [
        {"date": d["date"], "theme": d.get("theme"),
         "places": [names.get(s["placeId"], {}).get("name", "") for s in d["stops"]]} for d in seq["days"]]}
    if is_multi(spec):
        facts["route"] = _route_text(spec)
        facts["hotels"] = [{"city": st.get("city"), "name": st.get("name")} for st in stays]
        for fd, d in zip(facts["days"], seq["days"]):
            fd["city"] = d.get("city")
            if d.get("transfer"):
                fd["travel"] = d["transfer"].get("summary")
    for fd, d in zip(facts["days"], seq["days"]):
        if d.get("dayTrip"):
            fd["dayTrip"] = d["dayTrip"]["place"]
    try:
        out = ask_json(INTAKE_MODEL, get_prompt("NARRATIVE").format(spec=_sj(spec), facts=json.dumps(facts, ensure_ascii=False)),
                       "Write the text.", usage, max_tokens=1200, temperature=0.5)
    except Exception as e:  # noqa: BLE001
        print("narrative failed:", e)
        out = {}
    out = {"title": out.get("title") or f"{spec['destination']} trip",
           "summary": out.get("summary") or "", "dayNotes": out.get("dayNotes") or {}}
    if os.environ.get("GUARDRAIL_ID") and out["summary"]:
        out = _ground(out, facts, spec)
    return out


def _ground(out, facts, spec):
    """Bedrock Guardrails contextual grounding check (Phase 7.1). Falls back to plain text if blocked."""
    bedrock, _, _ = _clients()
    text = out["summary"] + "\n" + "\n".join(out["dayNotes"].values())
    try:
        r = bedrock.apply_guardrail(
            guardrailIdentifier=os.environ["GUARDRAIL_ID"],
            guardrailVersion=os.environ.get("GUARDRAIL_VERSION", "DRAFT"), source="OUTPUT",
            content=[{"text": {"text": json.dumps(facts), "qualifiers": ["grounding_source"]}},
                     {"text": {"text": f"Describe a {spec.get('tripType', '')} trip to {spec['destination']}",
                               "qualifiers": ["query"]}},
                     {"text": {"text": text, "qualifiers": ["guard_content"]}}])
        if r.get("action") == "GUARDRAIL_INTERVENED":
            print("guardrail intervened; using plain narrative")
            return {"title": out["title"], "summary": f"Your {spec['destination']} itinerary, built from verified "
                                                      f"flight, hotel and place data.", "dayNotes": {}}
    except Exception as e:  # noqa: BLE001
        print("guardrail check failed:", e)
    return out


def to_web(spec, seq, pool, verify_res, judge_res, caveats, story, pdf, trip_id, rounds, version=1):
    names = {p["placeId"]: p for p in pool["places"]}
    days = []
    for i, d in enumerate(seq["days"]):
        dt = date.fromisoformat(d["date"])
        title = (f"Day {i + 1} — {dt:%a %d %b}" + (f" · {d['city']}" if d.get("city") else "")
                 + (f" · day trip to {d['dayTrip']['place']}" if d.get("dayTrip") else "")
                 + (f" · {d['theme']}" if d.get("theme") else ""))
        stops = []
        t = d.get("transfer")
        if t:
            dep, arr = (t.get("departAt") or "")[11:16], (t.get("arriveAt") or "")[11:16]
            stops.append({"time": dep, "name": f"{'✈' if t.get('mode') == 'flight' else '🚆'} {t.get('summary')}",
                          "note": (f"arrive {arr}" if arr else "") + (" · estimated, book separately"
                                                                       if t.get("estimate") else ""),
                          "url": t.get("bookingUrl")})
        trip = d.get("dayTrip")
        if trip:
            first = (d["stops"][0].get("arriveAt") if d["stops"] else "") or ""
            leave = _fmt(_hm(first) - trip["travelMinutes"] - 10) if first else ""
            stops.append({"time": leave, "name": f"🚆 Day trip to {trip['place']}",
                          "note": f"about {trip['travelMinutes']} min each way by {trip['travelBy']}; back to the "
                                  f"hotel in the evening", "url": None})
        for s in d["stops"]:
            p = names.get(s["placeId"], {})
            note = f"{s['travelMinutesFromPrev']} min {s.get('travelBy', '')}".strip() if s.get("travelMinutesFromPrev") else ""
            stops.append({"time": s.get("arriveAt", ""), "name": p.get("name", s["placeId"]), "note": note,
                          "url": p.get("url") or p.get("mapsUrl")})
        days.append({"title": title, "stops": stops, "note": story["dayNotes"].get(d["date"], "")})
    totals = (verify_res or {}).get("totals", {})
    return {"tripId": trip_id, "title": story["title"], "summary": story["summary"],
            "totals": {"total": totals.get("totalSGD", 0), "budget": float(spec["budgetSGD"])},
            "days": days, "caveats": caveats, "pdfUrl": (pdf or {}).get("url"),
            "verified": bool((verify_res or {}).get("passed")),
            "judge": {"scores": (judge_res or {}).get("scores"), "rationale": (judge_res or {}).get("rationale")},
            "repairRounds": rounds, "version": version, "route": _route_text(spec),
            "hotels": [{"city": st.get("city"), "name": st.get("name"), "booked": bool(st.get("booked"))}
                       for st in _stays_of(seq, pool)]}


def save(user_id, trip_id, request, spec, seq, verify_res, judge_res, story, pdf, metrics, version=1):
    _, ddb, _ = _clients()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        ddb.Table(os.environ["TRIPS_TABLE"]).put_item(Item={
            "userId": user_id, "tripId": trip_id, "title": story["title"], "destination": spec["destination"],
            "startDate": spec["startDate"], "endDate": spec["endDate"], "status": "planned", "createdAt": now,
            "budgetSGD": str(spec["budgetSGD"]), "request": request[:2000], "tripSpec": json.dumps(spec)})
        item = {"tripId": trip_id, "version": str(version), "createdAt": now, "itinerary": json.dumps(seq),
                "verify": json.dumps(verify_res or {}), "judge": json.dumps(judge_res or {}),
                "narrative": json.dumps(story), "metrics": json.dumps(metrics)}
        if pdf and pdf.get("s3Key"):
            item["pdfKey"] = pdf["s3Key"]
        ddb.Table(os.environ["ITINERARIES_TABLE"]).put_item(Item=item)
    except KeyError as e:
        print("not saving: missing env", e)
    except Exception as e:  # noqa: BLE001
        print("save failed:", e)


# ====================================================================== the loop
CHANGE_FIELDS = {"startDate", "endDate", "pax", "budgetSGD", "destination", "destinationAirport", "originAirport",
                 "interests", "activities", "pace", "cuisine", "hotelPreference", "mustInclude", "avoid", "legs",
                 "preferences", "dayTrips"}
REGATHER_FIELDS = {"startDate", "endDate", "pax", "destination", "destinationAirport", "originAirport", "legs"}
PLACE_PREF_KEYS = {"vibe", "dietary", "accessibility", "audience", "indoorPreferred", "freeOnly", "avoid"}


def merge_prefs(old, new):
    """Router sends only the preferences that change; merge them into the current ones (one level deep)."""
    out = json.loads(json.dumps(old or {}))
    for k, v in (new or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def improve(spec, pool, plan, full_request, usage, change=None):
    """Steps 4-7: sequence -> verify -> judge -> repair, up to MAX_REPAIR_ROUNDS. Returns the best version."""
    _, _, gw = _clients()
    tspec = trip_spec_for_tools(spec)
    best, rounds = None, 0
    while True:
        yield ev("status", "Ordering each day around opening hours and travel times…")
        seq, unscheduled = sequence(spec, pool, plan)
        yield ev("status", "Checking budget, dates and timings…")
        vres = gw.call("verify_itinerary", {"tripSpec": tspec, "itinerary": seq})
        if not vres.get("ok"):
            vres = {"passed": False, "violations": [{"check": "verify_error", "message": vres.get("message", "")}]}
        judge_res = None
        if vres.get("passed") and LOOP_ENABLED:
            yield ev("status", "Reviewing how well it fits your preferences…")
            try:
                judge_res = judge(full_request, spec, seq, pool, usage)
            except Exception as e:  # noqa: BLE001
                print("judge failed:", e)
                judge_res = {"scores": {}, "minScore": 0, "passed": True, "rationale": "judge unavailable"}
        score = (bool(vres.get("passed")), (judge_res or {}).get("minScore", 0),
                 -len(vres.get("violations", [])) - len(unscheduled))
        if best is None or score > best["score"]:
            best = {"score": score, "plan": plan, "seq": seq, "verify": vres, "judge": judge_res,
                    "unscheduled": unscheduled}
        done = vres.get("passed") and (judge_res is None or judge_res.get("passed")) and not unscheduled
        if done or not LOOP_ENABLED or rounds >= MAX_REPAIR_ROUNDS:
            break
        rounds += 1
        why = ("hard checks" if not vres.get("passed")
               else "preference fit" if judge_res and not judge_res.get("passed") else "timing")
        yield ev("status", f"Improving the plan (round {rounds} of {MAX_REPAIR_ROUNDS}, fixing {why})…")
        try:
            plan = repair(spec, pool, plan, vres, judge_res, unscheduled, usage, change=change)
        except Exception as e:  # noqa: BLE001
            print("repair failed:", e)
            break
    best["rounds"] = rounds
    return best


def deliver(state, best, usage, metrics, user_id, t0):
    """Step 8: caveats, narrative, PDF, save (new version), stream the itinerary."""
    _, _, gw = _clients()
    spec, pool = state["spec"], state["pool"]
    seq, vres, judge_res = best["seq"], best["verify"], best["judge"]
    caveats = []
    if not vres.get("passed"):
        for v in vres.get("violations", [])[:6]:
            caveats.append(v["message"][0].upper() + v["message"][1:] + ".")
    for u in best["unscheduled"][:4]:
        caveats.append(f"{u.get('name', u['placeId'])} didn't fit on {u['date']} and was left out.")
    if any("mock" in s_ for s_ in metrics.get("dataSources", state.get("sources", []))):
        caveats.append("Some results are sample data (mock mode), not real prices or availability.")
    for t in spec.get("dayTrips") or []:
        if t.get("notFound"):
            caveats.append(f"I couldn't find places for a day trip to {t['place']}.")
    if vres.get("totals", {}).get("dailySpendAssumption"):
        caveats.append(f"Total includes an estimate of {vres['totals']['dailySpendAssumption']}.")

    yield ev("status", "Writing your trip summary…")
    story = narrative(spec, seq, pool, usage)
    state["version"] = state.get("version", 0) + 1
    state.setdefault("tripId", f"trip_{datetime.now(timezone.utc):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}")
    trip_id, version = state["tripId"], state["version"]
    yield ev("status", "Creating your PDF…")
    pdf = gw.call("render_pdf", {"tripId": trip_id, "version": version, "title": story["title"],
                                 "tripSpec": trip_spec_for_tools(spec), "itinerary": seq, "narrative": story,
                                 "caveats": caveats})
    if not pdf.get("ok"):
        print("render_pdf failed:", pdf)
        pdf = None
    metrics.update(latencyMs=int((time.time() - t0) * 1000), repairRounds=best["rounds"],
                   hardChecksPassed=bool(vres.get("passed")), judgeMinScore=(judge_res or {}).get("minScore"),
                   inputTokens=usage.input, outputTokens=usage.output, modelCalls=usage.calls, version=version,
                   cacheReadTokens=usage.cache_read, cacheWriteTokens=usage.cache_write,
                   models={"planner": effective_model(PLANNER_MODEL), "intake": effective_model(INTAKE_MODEL),
                           "judge": effective_model(JUDGE_MODEL), "plannerEffort": PLANNER_EFFORT})
    print(json.dumps({"metric": "plan_completed", "tripId": trip_id, **metrics}))
    save(user_id, trip_id, state["request"], spec, seq, vres, judge_res, story, pdf, metrics, version)
    state.update(plan=best["plan"], seq=seq, story=story, verify=vres)
    web = to_web(spec, seq, pool, vres, judge_res, caveats, story, pdf, trip_id, best["rounds"], version)
    yield ev("itinerary", itinerary=web)


def _summary(state):
    """Short text version of the current plan, for the router and chat prompts."""
    pool, seq = state["pool"], state["seq"]
    flight = next((f for f in pool["flights"] if f["offerId"] == seq["flightOfferId"]), {})
    lines = []
    if is_multi(state["spec"]):
        lines.append("Route: " + _route_text(state["spec"]))
    lines.append(f"Flight: {flight.get('airline')} SGD {flight.get('priceTotalSGD')} total; "
                 + "; ".join(f"{s.get('from')}->{s.get('to')} departs {s.get('departAt')}" for s in flight.get("slices", [])))
    t_by_id = {t["transferId"]: t for t in pool.get("transfers", [])}
    for t in seq.get("transfers") or []:
        o = t_by_id.get(t["transferId"], {})
        lines.append(f"Transfer {t['date']}: {o.get('summary')} SGD {o.get('priceTotalSGD')}"
                     + (" (estimate)" if o.get("estimate") else ""))
    totals = (state.get("verify") or {}).get("totals", {})
    if totals:
        lines.append(f"Estimated total SGD {totals.get('totalSGD')} of budget SGD {totals.get('budgetSGD')}")
    return "\n".join(lines + _plan_lines(seq, pool, with_prices=True))


def _history_text(state, n=6):
    h = state.get("history", [])[-n:]
    return "\n".join(f"{role}: {text[:400]}" for role, text in h) or "(none)"


def _remember(state, role, text):
    state.setdefault("history", []).append((role, text))
    state["history"] = state["history"][-20:]


def _check_spec(spec):
    problems = []
    try:
        s, e = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
        if e < s:
            problems.append("the end date is before the start date")
        if s < date.today():
            problems.append("the start date is in the past")
        if (e - s).days > 20:
            problems.append("trips can be at most 21 days")
    except (KeyError, TypeError, ValueError):
        problems.append("the dates are unclear")
    if not spec.get("budgetSGD") or float(spec["budgetSGD"]) <= 0:
        problems.append("the budget is missing")
    if not spec.get("pax") or int(spec["pax"]) < 1:
        problems.append("the number of travellers is missing")
    return problems


def new_plan(state, request, user_id, usage, metrics, t0):
    """First plan for a trip (or a full re-plan after big changes)."""
    spec = state["spec"]
    nights = (date.fromisoformat(spec["endDate"]) - date.fromisoformat(spec["startDate"])).days
    if is_multi(spec):
        yield ev("status", f"Planning a {nights + 1}-day multi-city trip: {_route_text(spec)}, for {spec['pax']}, "
                           f"budget SGD {float(spec['budgetSGD']):,.0f}.")
        pool = yield from gather_multi(spec, usage, metrics)
        gaps = multi_pool_gaps(spec, pool)
        if gaps or not pool["places"]:
            yield ev("message", f"I couldn't find {', '.join(gaps or ['any places to visit'])} for this route. "
                                f"Could you try different dates, another airport, or a different order of cities?")
            return False
        state.update(pool=pool, sources=metrics.get("dataSources", []))
        yield ev("status", f"Found {len(pool['flights'])} international flights, {len(pool['stays'])} hotels in "
                           f"{len(spec['legs'])} cities, {len(pool['transfers'])} transfer options and "
                           f"{len(pool['places'])} places.")
    else:
        yield ev("status", f"Planning {nights + 1} days in {spec['destination']} for {spec['pax']}, "
                           f"budget SGD {float(spec['budgetSGD']):,.0f}.")
        pool = yield from gather(spec, usage, metrics)
        if not (pool["flights"] and pool["stays"] and pool["places"]):
            missing = [k for k in ("flights", "stays", "places") if not pool[k]]
            yield ev("message", f"I couldn't find any {', '.join(missing)} for this trip. "
                                f"Could you try different dates or a nearby airport?")
            return False
        state.update(pool=pool, sources=metrics.get("dataSources", []))
        yield ev("status", f"Found {len(pool['flights'])} flights, {len(pool['stays'])} hotels and "
                           f"{len(pool['places'])} places.")
    yield ev("status", "Drafting your itinerary…")
    plan = propose(spec, pool, usage)
    best = yield from improve(spec, pool, plan, state["request"], usage)
    yield from deliver(state, best, usage, metrics, user_id, t0)
    totals = best["verify"].get("totals", {})
    msg = (f"Here's your {nights + 1}-day {spec['destination']} plan"
           + (f" with {len(spec['legs'])} hotels and {len(spec['legs']) - 1} "
                f"transfer{'s' if len(spec['legs']) > 2 else ''}" if is_multi(spec) else "")
           + (f", estimated SGD {totals['totalSGD']:,.0f} of your SGD {float(spec['budgetSGD']):,.0f} budget"
              if totals.get("totalSGD") else "")
           + ". Want any changes? For example: “make day 3 more relaxed”, “find a cheaper hotel”, "
             "“add a sushi dinner”, or ask me anything about the plan.")
    if spec.get("notFound"):
        msg += f" (I couldn't find {', '.join(spec['notFound'])} on the map; tell me the area and I'll add it.)"
    _remember(state, "assistant", msg)
    yield ev("message", msg)
    return True


def search_more(state, spec, wanted, metrics):
    """Targeted find_places for new kinds of places (e.g. 'bouldering gym', 'hiking'), added to the pool.
    Flights and hotels stay as they are."""
    _, _, gw = _clients()
    cats = sorted({w.lower().strip() for w in wanted if w.lower().strip() in PLACE_CATEGORIES})
    kws = [w for w in wanted if w.lower().strip() not in PLACE_CATEGORIES][:5]
    if spec.get("cuisine") and "restaurant" not in cats and any("restaurant" in w or "food" in w for w in wanted):
        cats.append("restaurant")
    base = with_prefs("find_places", spec, {"categories": cats, "keywords": kws, "radiusKm": 8})
    seq_stays = {x["stayId"]: x for x in (state.get("seq") or {}).get("stays") or []}
    by_id = {s_["stayId"]: s_ for s_ in state["pool"]["stays"]}
    if is_multi(spec):
        targets = []
        for i, leg in enumerate(spec["legs"]):
            sid = next((k for k, v in seq_stays.items() if v.get("city") == leg["city"]), None)
            targets.append((i, leg["city"], by_id.get(sid)))
    else:
        targets = [(None, spec["destination"], by_id.get((state.get("seq") or {}).get("stayId")))]
    res, found_any = {"ok": False}, []
    for i, city, stay in targets:
        yield ev("status", f"Looking for {', '.join(list(cats) + kws)} in {city}…")
        args = dict(base)
        if stay and stay.get("lat") is not None:
            args.update(lat=stay["lat"], lng=stay["lng"])     # search around the hotel
        else:
            args["city"] = city
        try:
            r = gw.call("find_places", args)
        except GatewayError as e:
            r = {"ok": False, "message": str(e)}
        metrics.setdefault("toolCalls", []).append("find_places")
        if r.get("ok"):
            res = {"ok": True, "source": r.get("source")}
            tag = {} if i is None else {"city": city, "leg": i}
            found_any += [{**p, **tag} for p in r.get("places", [])]
    res["places"] = found_any
    found = res.get("places", []) if res.get("ok") else []
    fresh = {p["placeId"]: p for p in found}
    have = {p["placeId"] for p in state["pool"]["places"]}
    new = [p for p in found if p["placeId"] not in have]
    # places found again get their latest tags/matches (e.g. after the traveller added a dietary need)
    state["pool"]["places"] = new + [fresh.get(p["placeId"], p) for p in state["pool"]["places"]]
    if res.get("ok"):
        metrics.setdefault("dataSources", []).append(f"find_places:{res.get('source')}")
    found = sorted({p.get("activity") or p.get("category") for p in new})
    missing = [w for w in wanted if not any(w.lower() in (p.get("activity") or "") or w.lower() == p.get("category")
                                            for p in new)]
    yield ev("status", f"Found {len(new)} new places" + (f" ({', '.join(found)})" if found else "") + ".")
    return new, missing


def refresh_bookings(state, spec, what, metrics):
    """Search hotels / flights / transfers again with the new wishes, keeping the rest of the pool (cheaper than
    a full re-plan). what: subset of {"stays", "flights", "transfers"}."""
    _, _, gw = _clients()
    pool, multi = state["pool"], is_multi(spec)
    calls = metrics.setdefault("toolCalls", [])

    def call(tool, args):
        calls.append(tool)
        try:
            r = gw.call(tool, with_prefs(tool, spec, args))
        except GatewayError as e:
            r = {"ok": False, "message": str(e)}
        if r.get("ok"):
            metrics.setdefault("dataSources", []).append(f"{tool}:{r.get('source')}")
        return r

    if "stays" in what:
        yield ev("status", "Searching hotels again with your new wishes…")
        new = []
        if multi:
            for i, leg in enumerate(spec["legs"]):
                name, booked, price = _hotel_wish(spec, leg)
                extra = {"name": name, "alreadyBooked": booked, **({"bookedPriceSGD": price} if price else {})} \
                    if name else {}
                r = call("search_stays", {"city": leg["city"], "checkIn": leg["checkIn"], "checkOut": leg["checkOut"],
                                          "guests": spec["pax"], **extra})
                new += [{**x, "city": leg["city"], "leg": i} for x in r.get("stays", [])] if r.get("ok") else []
        else:
            r = call("search_stays", {"city": spec["destination"], "checkIn": spec["startDate"],
                                      "checkOut": spec["endDate"], "guests": spec["pax"]})
            new = r.get("stays", []) if r.get("ok") else []
        if new:
            pool["stays"] = new
    if "flights" in what:
        yield ev("status", "Searching flights again with your new wishes…")
        args = {"origin": spec["originAirport"], "destination": spec["legs"][0]["airport"] if multi
                else spec["destinationAirport"], "departDate": spec["startDate"], "returnDate": spec["endDate"],
                "pax": spec["pax"]}
        if multi:
            args["returnFrom"] = spec["legs"][-1]["airport"]
        r = call("search_flights", args)
        if r.get("ok") and r.get("offers"):
            pool["flights"] = r["offers"]
    if "transfers" in what and multi:
        yield ev("status", "Checking the transfers between cities again…")
        new = []
        for i, (a, b) in enumerate(zip(spec["legs"], spec["legs"][1:])):
            r = call("search_transfers", {"from": {"city": a["city"], "airport": a["airport"]},
                                          "to": {"city": b["city"], "airport": b["airport"]},
                                          "date": a["checkOut"], "pax": spec["pax"]})
            new += [{**o, "hop": i} for o in r.get("options", [])] if r.get("ok") else []
        if new:
            pool["transfers"] = new


def revise(state, instruction, changes, user_id, usage, metrics, t0, search_for=None):
    """Apply a change request to the current trip."""
    old = state["spec"]
    spec = json.loads(json.dumps(old))
    changes = dict(changes or {})
    new_prefs = changes.pop("preferences", None) if isinstance(changes.get("preferences"), dict) else None
    new_must = changes.pop("mustInclude", None)
    spec.update({k: v for k, v in changes.items() if k in CHANGE_FIELDS and v not in (None, "")})
    refresh = set()
    if changes.get("destination") and str(changes["destination"]).strip().lower() != str(old.get("destination")).lower():
        if "legs" not in changes:
            spec["legs"] = []          # a new single destination replaces the old route
        # the old trip's named places, day trips and named hotel belonged to the old destination
        spec["mustInclude"], spec["pinned"], spec["notFound"] = [], [], []
        if "dayTrips" not in changes:
            spec["dayTrips"] = []
        if not new_prefs or "hotel" not in new_prefs:
            hp = (spec.get("preferences") or {}).get("hotel") or {}
            hp.update(name="", booked=False, bookedPriceSGD=0, area="")
        if not re.fullmatch(r"[A-Z]{3}", str(changes.get("destinationAirport") or "").upper()) or \
                changes.get("destinationAirport") == old.get("destinationAirport"):
            spec["destinationAirport"] = resolve_airport(spec["destination"], usage) or ""
            changes["destinationAirport"] = spec["destinationAirport"]
        if not spec["destinationAirport"] and not spec.get("legs"):
            msg = f"Which airport should I fly you into for {spec['destination']}?"
            _remember(state, "assistant", msg)
            yield ev("message", msg)
            return
    for leg in spec.get("legs") or []:
        if isinstance(leg, dict) and leg.get("city") and not re.fullmatch(r"[A-Z]{3}", str(leg.get("airport") or "")):
            leg["airport"] = resolve_airport(leg["city"], usage)
    if new_must:
        spec["mustInclude"] = list(spec.get("mustInclude") or []) + (new_must if isinstance(new_must, list) else [new_must])
    normalize_special(spec)
    if new_prefs or "avoid" in changes:
        spec["preferences"] = merge_prefs(old.get("preferences"), new_prefs)
        normalize_prefs(spec)
        old_p = old.get("preferences") or {}
        if spec["preferences"].get("hotel") != old_p.get("hotel"):
            refresh.add("stays")
        if spec["preferences"].get("flight") != old_p.get("flight"):
            refresh.add("flights")
        if spec["preferences"].get("transfer") != old_p.get("transfer"):
            refresh.add("transfers")
        if any(spec["preferences"].get(k) != old_p.get(k) for k in PLACE_PREF_KEYS | {"dayStart", "dayEnd"}):
            search_for = list(search_for or []) + [c for c in spec.get("interests", []) if c in PLACE_CATEGORIES][:4] \
                + ["restaurant"]
    if "legs" in changes and not set(changes) & {"destination", "startDate", "endDate"}:
        # same cities and dates, only a per-leg hotel wish changed -> hotels only
        if [(l.get("city"), l.get("nights")) for l in spec.get("legs") or []] == \
                [(l.get("city"), l.get("nights")) for l in old.get("legs") or []]:
            changes.pop("legs")
            if [l.get("hotel") for l in spec["legs"]] != [l.get("hotel") for l in old.get("legs") or []]:
                refresh.add("stays")
    problems = _check_spec(spec)
    if not problems and set(changes or {}) & {"legs", "startDate", "endDate"} and spec.get("legs"):
        problems = normalize_legs(spec)    # re-split nights and dates across the cities
    if problems:
        msg = f"I can't make that change yet: {'; '.join(problems)}. Could you clarify?"
        _remember(state, "assistant", msg)
        yield ev("message", msg)
        return
    state["specBefore"] = state["spec"]
    state["spec"] = spec
    state["request"] = (state["request"] + " | Change: " + instruction).strip()
    if set(changes or {}) & REGATHER_FIELDS:
        yield ev("status", "That changes the basics of the trip, so I'm searching again…")
        if set(changes) & {"destination", "destinationAirport"}:
            # a different trip: new tripId, version 1
            state.pop("tripId", None)
            state.pop("version", None)
        yield from new_plan(state, state["request"], user_id, usage, metrics, t0)
        return
    if refresh:
        yield from refresh_bookings(state, spec, refresh, metrics)
        if "stays" in refresh:
            instruction += (" The hotel options were searched again: choose a stayId from the new list (the one with "
                            "matches 'requested hotel' or 'already booked' if there is one).")
        if "flights" in refresh:
            instruction += " The flights were searched again: choose a flightOfferId from the new list."
    specials = set()
    if new_must:
        specials.add("places")
    if "dayTrips" in changes:
        specials.add("dayTrips")
    if specials:
        calls, sources = metrics.setdefault("toolCalls", []), set()
        yield from gather_specials(spec, state["pool"], calls, sources, only=specials)
        metrics.setdefault("dataSources", []).extend(sorted(sources))
        if "places" in specials and spec.get("pinned"):
            instruction += " Include the pinned places: " + ", ".join(
                f"{x['name']} ({x['placeId']})" for x in spec["pinned"]) + "."
        if "dayTrips" in specials:
            instruction += " Plan the day trips: " + ", ".join(
                t["place"] + (f" on {t['date']}" if t.get("date") else "") for t in spec["dayTrips"]
                if t.get("lat") is not None) + "."
    # New kinds of places requested? Look them up first (otherwise the planner has nothing new to use).
    wanted = list(search_for or [])
    old_interests = set(state.get("specBefore", {}).get("interests") or [])
    for k in ("interests", "activities"):
        for w in (changes or {}).get(k) or []:
            if w and w not in old_interests and w not in wanted and w != "restaurant":
                wanted.append(w)
    missing = []
    if wanted:
        new, missing = yield from search_more(state, spec, wanted[:6], metrics)
        if new:
            instruction += (" Use the newly found places for this (pool items with category or activity "
                            f"{', '.join(sorted({p.get('activity') or p.get('category') for p in new}))}).")
    yield ev("status", "Updating your plan…")
    plan = repair(spec, state["pool"], state["plan"], None, None, [], usage, change=instruction)
    best = yield from improve(spec, state["pool"], plan, state["request"], usage, change=instruction)
    yield from deliver(state, best, usage, metrics, user_id, t0)
    note = (best["plan"].get("reasoning") or "I've updated the plan.").strip()
    if missing:
        note += f" I couldn't find any {', '.join(missing)} near your trip, so those aren't included."
    if new_must and spec.get("notFound"):
        note += f" I couldn't find {', '.join(spec['notFound'])} on the map, so I couldn't add it; could you give " \
                f"the area or full name?"
    msg = f"{note} (This is version {state['version']}; ask for more changes any time.)"
    _remember(state, "assistant", msg)
    yield ev("message", msg)


def answer(state, question, usage):
    system = get_prompt("CHAT").format(spec=_sj(state["spec"]), facts=_summary(state),
                                       history=_history_text(state))
    resp = converse(INTAKE_MODEL, system, [{"role": "user", "content": [{"text": question}]}], usage,
                    max_tokens=500, temperature=0.4)
    text = reply_text(resp).strip() or "Sorry, I couldn't answer that. Could you rephrase?"
    _remember(state, "assistant", text)
    yield ev("message", text)


def route(state, message, usage):
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    system = get_prompt("ROUTER").format(today=today, spec=_sj(state["spec"]), summary=_summary(state),
                                         history=_history_text(state))
    try:
        r = ask_json(INTAKE_MODEL, system, message, usage, max_tokens=900, temperature=0.0)
    except Exception as e:  # noqa: BLE001
        print("router failed:", e)
        r = {"intent": "modify", "specChanges": {}, "instruction": message}
    if r.get("intent") not in ("modify", "question", "new_trip", "smalltalk"):
        r["intent"] = "question"
    r["specChanges"] = {k: v for k, v in (r.get("specChanges") or {}).items() if k in CHANGE_FIELDS}
    if "interests" in r["specChanges"]:   # keep only known categories; others become activities
        ints = [str(x).lower().strip() for x in r["specChanges"]["interests"] or []]
        extra = [x for x in ints if x not in PLACE_CATEGORIES]
        r["specChanges"]["interests"] = [x for x in ints if x in PLACE_CATEGORIES] or state["spec"].get("interests")
        if extra:
            r["specChanges"]["activities"] = list(dict.fromkeys(list(r["specChanges"].get("activities") or [])
                                                                + extra))[:5]
    r["instruction"] = r.get("instruction") or message
    r["searchFor"] = [str(x).strip()[:40] for x in (r.get("searchFor") or []) if str(x).strip()][:6]
    return r


def chat_turn(message, session_id, user_id):
    """One user message -> streamed events. State lives per Runtime session (same microVM)."""
    t0 = time.time()
    usage = Usage()
    metrics = {"sessionId": session_id, "loop": LOOP_ENABLED}
    state = SESSIONS.setdefault(session_id, {})
    _remember(state, "user", message)

    if state.get("plan"):
        yield ev("status", "Reading your message…")
        r = route(state, message, usage)
        metrics["intent"] = r["intent"]
        if r["intent"] == "question":
            yield from answer(state, message, usage)
            return
        if r["intent"] == "smalltalk":
            text = ("Happy to help! You can ask me to change anything in the plan, for example a cheaper hotel, "
                    "a more relaxed day, or more food spots, or ask a question about it.")
            _remember(state, "assistant", text)
            yield ev("message", text)
            return
        if r["intent"] == "modify":
            yield from revise(state, r["instruction"], r["specChanges"], user_id, usage, metrics, t0,
                              search_for=r["searchFor"])
            return
        # new_trip: start over, but keep the conversation, dates, travellers, budget, origin and preferences
        # (intake replaces whatever the new message changes)
        history, old, memory = state.get("history", []), state.get("spec") or {}, state.get("memory")
        carry = {k: old[k] for k in ("startDate", "endDate", "pax", "budgetSGD", "originAirport", "pace") if old.get(k)}
        if old.get("preferences"):
            prefs = json.loads(json.dumps(old["preferences"]))
            prefs.get("hotel", {}).update(name="", booked=False, bookedPriceSGD=0, area="")
            carry["preferences"] = prefs
        state.clear()
        state.update(history=history, carry=carry)
        if memory is not None:
            state["memory"] = memory

    # Intake (first message, or answer to a clarifying question, or a new trip)
    yield ev("status", "Understanding your request…")
    if "memory" not in state:
        state["memory"] = memory_recall(user_id, session_id, message)
        if state["memory"]:
            yield ev("status", "Remembering your preferences from earlier trips…")
    intake_text = message
    if state["memory"]:
        intake_text = (message + "\n\nPreferences remembered from this traveller's earlier trips "
                       "(use them when they fit; anything in the request above wins): "
                       + "; ".join(state["memory"]))
    carry = state.pop("carry", None)
    spec = intake(intake_text, state.get("pendingSpec") or carry, usage, carried=bool(carry) and not state.get("pendingSpec"))
    if state["memory"]:
        spec["rememberedPreferences"] = state["memory"]
    state["request"] = (state.get("pendingRequest", "") + " " + message).strip()
    if spec["missing"]:
        state.update(pendingSpec=spec, pendingRequest=state["request"])
        q = spec.get("question") or f"Could you tell me the {', '.join(spec['missing'])}?"
        _remember(state, "assistant", q)
        yield ev("question", q)
        return
    state.pop("pendingSpec", None)
    state.pop("pendingRequest", None)
    state["spec"] = spec
    yield from new_plan(state, message, user_id, usage, metrics, t0)


# ====================================================================== AgentCore entrypoint
@app.entrypoint
def invoke(payload, context=None):
    """payload: {"prompt": "...", "reset": false}; streams events. One Runtime session = one conversation."""
    payload = payload if isinstance(payload, dict) else json.loads(payload or "{}")
    prompt = str(payload.get("prompt", "")).strip()
    session_id = getattr(context, "session_id", None) or payload.get("sessionId") or "local"
    if payload.get("reset"):
        SESSIONS.pop(session_id, None)
    if not prompt:
        yield ev("message", "Hi! Tell me about the trip you'd like: where, when, how many people and your "
                            "budget in SGD.")
        return
    try:
        user_id = _user_from_context(context, payload)
        turns = [("USER", prompt[:4000])]
        for e in chat_turn(prompt[:4000], session_id, user_id):
            if e["type"] in ("message", "question"):
                turns.append(("ASSISTANT", e["message"]))
            elif e["type"] == "itinerary":
                it = e["itinerary"]
                turns.append(("ASSISTANT", f"[Itinerary v{it.get('version')}] {it.get('title')}: {it.get('summary', '')} "
                                           + " | ".join(d["title"] + ": " + ", ".join(s_["name"] for s_ in d["stops"])
                                                        for d in it.get("days", []))))
            yield e
        memory_save(user_id, session_id, turns)
    except GatewayError as e:
        print("gateway error:", e)
        yield ev("error", f"A planning tool is unavailable right now ({e}). Please try again.")
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        yield ev("error", f"Something went wrong while planning ({type(e).__name__}). Please try again.")


if __name__ == "__main__":
    app.run()
