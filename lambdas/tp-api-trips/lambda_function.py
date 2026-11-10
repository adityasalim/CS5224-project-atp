"""
tp-api-trips — REST API backend (behind API Gateway + Cognito authorizer) for trip history,
PDF links, feedback, stated preferences and chat history.

Routes (API Gateway REST API, Lambda proxy integration):
  GET  /trips                       list my trips, newest first
  GET  /trips/{tripId}              one trip + its latest itinerary
  GET  /trips/{tripId}/pdf          fresh 15-minute download link for the latest PDF
                                    (?version=N for a specific version)
  POST /trips/{tripId}/feedback     {"rating": 1-5, "comments": "...", "version": 2, "swaps": [...],
                                     optional 1-5: usefulness, personalization, trust, easeOfUse, satisfaction}
  GET  /me/preferences              my stated preferences
  PUT  /me/preferences              {"homeCity": "...", "homeAirport": "SIN", ...}
  GET  /chats                       my chat sessions, newest first (read from AgentCore Memory)
  GET  /chats/{sessionId}           the messages of one of my chat sessions

The user is ALWAYS taken from the Cognito token (requestContext.authorizer.claims.sub),
never from the request body, so nobody can read another user's trips or chats.

Environment variables:
  TRIPS_TABLE        tp-dev-trips        (PK userId, SK tripId)
  ITINERARIES_TABLE  tp-dev-itineraries  (PK tripId, SK version)
  FEEDBACK_TABLE     tp-dev-feedback     (PK tripId, SK createdAt)
  USERS_TABLE        tp-dev-users        (PK userId)
  PLANS_BUCKET       tp-dev-plans-<suffix>
  MEMORY_ID          AgentCore Memory id (the same one the agent uses); needed for /chats
  ALLOWED_ORIGINS    comma-separated, e.g. "https://dxxxx.cloudfront.net,http://localhost:5173"

IAM for /chats: bedrock-agentcore:ListSessions and bedrock-agentcore:ListEvents on the memory.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

_dynamodb = boto3.resource("dynamodb")
_s3 = None
_agentcore = None
PREF_FIELDS = {
    "homeCity": str, "homeAirport": str, "budgetStyle": str, "pace": str,
    "dietary": list, "interests": list, "notes": str,
}
MAX_TEXT = 1000
SESSION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$")
ITIN_RE = re.compile(r"^\[Itinerary v\d+\]\s*(.+?):\s")   # agent saves "[Itinerary v1] <title>: <summary>"
MAX_SESSIONS = 50
MAX_EVENT_PAGES = 5


# ---------------------------------------------------------------- response helpers
class _Enc(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return int(o) if o == o.to_integral_value() else float(o)
        return super().default(o)


def _origin(event):
    allowed = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    req = (event.get("headers") or {}).get("origin") or (event.get("headers") or {}).get("Origin")
    if req and req in allowed:
        return req
    return allowed[0] if allowed else "*"


def _resp(event, status, body):
    return {"statusCode": status,
            "headers": {"Access-Control-Allow-Origin": _origin(event),
                        "Access-Control-Allow-Headers": "Authorization,Content-Type",
                        "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS",
                        "Content-Type": "application/json"},
            "body": json.dumps(body, cls=_Enc)}


def _error(event, status, code, message):
    return _resp(event, status, {"error": code, "message": message})


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _table(env):
    return _dynamodb.Table(os.environ[env])


def _body(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64
        raw = base64.b64decode(raw).decode()
    return json.loads(raw)


# ---------------------------------------------------------------- data helpers
def _get_trip(user_id, trip_id):
    return _table("TRIPS_TABLE").get_item(Key={"userId": user_id, "tripId": trip_id}).get("Item")


def _latest_itinerary(trip_id):
    table = _table("ITINERARIES_TABLE")
    items, cursor = [], None
    while True:
        kwargs = {"KeyConditionExpression": Key("tripId").eq(trip_id)}
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            break
    if not items:
        return None

    def ver(i):  # versions are stored as strings ("1", "2", ..., "10"); compare as numbers
        try:
            return int(i.get("version", 0))
        except (TypeError, ValueError):
            return 0
    return max(items, key=ver)


def _presign(key):
    global _s3
    if _s3 is None:
        region = os.environ.get("AWS_REGION", "ap-southeast-1")
        _s3 = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                           config=Config(signature_version="s3v4"))
    return _s3.generate_presigned_url("get_object", Params={"Bucket": os.environ["PLANS_BUCKET"], "Key": key},
                                      ExpiresIn=900)


def _mem():
    global _agentcore
    if _agentcore is None:
        _agentcore = boto3.client("bedrock-agentcore", region_name=os.environ.get("AWS_REGION", "ap-southeast-1"))
    return _agentcore


def _iso(ts):
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return str(ts or "")


def _event_messages(ev):
    """Chat messages inside one AgentCore event: [{role, text, at}]."""
    out = []
    for part in ev.get("payload") or []:
        conv = part.get("conversational") or {}
        text = ((conv.get("content") or {}).get("text") or "").strip()
        if text:
            out.append({"role": str(conv.get("role") or "").lower() or "assistant", "text": text,
                        "at": _iso(ev.get("eventTimestamp"))})
    return out


def _session_events(user_id, session_id, max_pages=MAX_EVENT_PAGES):
    events, token = [], None
    for _ in range(max_pages):
        kwargs = {"memoryId": os.environ["MEMORY_ID"], "actorId": user_id, "sessionId": session_id,
                  "includePayloads": True, "maxResults": 100}
        if token:
            kwargs["nextToken"] = token
        page = _mem().list_events(**kwargs)
        events.extend(page.get("events", []))
        token = page.get("nextToken")
        if not token:
            break
    events.sort(key=lambda e: e.get("eventTimestamp") or datetime.min.replace(tzinfo=timezone.utc))
    return events


def _chat_title(msgs):
    """Plan title from the first '[Itinerary vN] <title>: ...' reply; else the first thing the user said."""
    for m in msgs:
        if m["role"] != "user":
            hit = ITIN_RE.match(m["text"])
            if hit:
                return hit.group(1).strip()[:80]
    return next((m["text"] for m in msgs if m["role"] == "user"), "")[:60]


def _user_records(user_id):
    """Query only this authenticated user's partition, including paginated receipts."""
    items, cursor = [], None
    while True:
        kwargs = {"KeyConditionExpression": Key("userId").eq(user_id), "ConsistentRead": True}
        if cursor:
            kwargs["ExclusiveStartKey"] = cursor
        page = _table("TRIPS_TABLE").query(**kwargs)
        items.extend(page.get("Items", []))
        cursor = page.get("LastEvaluatedKey")
        if not cursor:
            return items


def _public_run(item):
    return {key: item[key] for key in
            ("requestId", "sessionId", "status", "progress", "updatedAt", "resultTripId", "version", "choices")
            if key in item}


def _web_itinerary(trip, item):
    if not item:
        return None
    web = item.get("webItinerary")
    if isinstance(web, str):
        try:
            web = json.loads(web)
        except (ValueError, TypeError):
            web = None
    if not isinstance(web, dict):
        # Old records have the PDF key and narrative, but not a complete web card.
        try:
            story = json.loads(item.get("narrative") or "{}")
        except (ValueError, TypeError):
            story = {}
        web = {"title": trip.get("title") or story.get("title") or "Saved trip",
               "summary": story.get("summary") or "Open the PDF for the full archived itinerary.",
               "days": [], "status": trip.get("status")}
    web = dict(web, tripId=trip["tripId"], version=int(item["version"]), hasPdf=bool(item.get("pdfKey")))
    web.pop("pdfUrl", None)  # Never restore an expired presigned URL.
    return web


# ---------------------------------------------------------------- route handlers
def list_trips(event, user_id):
    trips = [{k: t.get(k) for k in ("tripId", "title", "destination", "startDate", "endDate", "status",
                                     "createdAt", "budgetSGD", "budget", "currency")}
             for t in _user_records(user_id) if t.get("recordType") != "planning_run"]
    trips.sort(key=lambda t: t.get("createdAt") or "", reverse=True)
    return _resp(event, 200, {"trips": trips[:50]})


def get_trip(event, user_id, trip_id):
    trip = _get_trip(user_id, trip_id)
    if not trip or trip.get("recordType") == "planning_run":
        return _error(event, 404, "not_found", "trip not found")
    itin = _latest_itinerary(trip_id)
    return _resp(event, 200, {"trip": trip, "itinerary": itin, "webItinerary": _web_itinerary(trip, itin)})


def get_pdf(event, user_id, trip_id):
    if not _get_trip(user_id, trip_id):
        return _error(event, 404, "not_found", "trip not found")
    version = str((event.get("queryStringParameters") or {}).get("version") or "").strip()
    if version.isdigit():
        itin = _table("ITINERARIES_TABLE").get_item(Key={"tripId": trip_id, "version": version}).get("Item")
    else:
        itin = _latest_itinerary(trip_id)
    if not itin or not itin.get("pdfKey"):
        return _error(event, 404, "no_pdf", "no PDF has been generated for this trip yet")
    return _resp(event, 200, {"url": _presign(itin["pdfKey"]), "expiresInSeconds": 900})


def post_feedback(event, user_id, trip_id):
    if not _get_trip(user_id, trip_id):
        return _error(event, 404, "not_found", "trip not found")
    try:
        body = _body(event)
        rating = int(body.get("rating"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return _error(event, 400, "bad_input", "rating must be a whole number from 1 to 5")
    if not 1 <= rating <= 5:
        return _error(event, 400, "bad_input", "rating must be a whole number from 1 to 5")
    swaps = body.get("swaps") or []
    if not isinstance(swaps, list) or len(swaps) > 50:
        return _error(event, 400, "bad_input", "swaps must be a list (max 50)")
    item = {"tripId": trip_id, "createdAt": _now(), "userId": user_id, "rating": rating,
            "comments": str(body.get("comments", ""))[:MAX_TEXT], "swaps": json.dumps(swaps)[:5000]}
    if isinstance(body.get("version"), int) and not isinstance(body.get("version"), bool):
        item["version"] = body["version"]          # which itinerary version was rated
    for dim in ("easeOfUse", "usefulness", "personalization", "trust", "satisfaction"):  # §5.2 user study
        if body.get(dim) is not None:
            try:
                v = int(body[dim])
            except (TypeError, ValueError):
                return _error(event, 400, "bad_input", f"{dim} must be 1-5")
            if not 1 <= v <= 5:
                return _error(event, 400, "bad_input", f"{dim} must be 1-5")
            item[dim] = v
    _table("FEEDBACK_TABLE").put_item(Item=item)
    return _resp(event, 201, {"saved": True, "createdAt": item["createdAt"]})


def get_prefs(event, user_id):
    item = _table("USERS_TABLE").get_item(Key={"userId": user_id}).get("Item") or {"userId": user_id}
    return _resp(event, 200, {"preferences": item})


def put_prefs(event, user_id):
    try:
        body = _body(event)
    except json.JSONDecodeError:
        return _error(event, 400, "bad_input", "body must be JSON")
    updates = {}
    for field, typ in PREF_FIELDS.items():
        if field not in body:
            continue
        val = body[field]
        if typ is str:
            updates[field] = str(val)[:MAX_TEXT]
        elif isinstance(val, list):
            updates[field] = [str(v)[:100] for v in val][:30]
        else:
            return _error(event, 400, "bad_input", f"{field} must be a list")
    unknown = set(body) - set(PREF_FIELDS)
    if not updates:
        return _error(event, 400, "bad_input", f"no recognised fields; use {sorted(PREF_FIELDS)}")
    updates["updatedAt"] = _now()
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)
    _table("USERS_TABLE").update_item(
        Key={"userId": user_id}, UpdateExpression=expr,
        ExpressionAttributeNames={f"#{k}": k for k in updates},
        ExpressionAttributeValues={f":{k}": v for k, v in updates.items()})
    return _resp(event, 200, {"saved": sorted(updates), "ignored": sorted(unknown)})


def _memory_chats(event, user_id):
    """My chat sessions, newest first. The title is the plan title, else the first thing I said."""
    sessions, token = [], None
    while len(sessions) < MAX_SESSIONS:
        kwargs = {"memoryId": os.environ["MEMORY_ID"], "actorId": user_id, "maxResults": MAX_SESSIONS}
        if token:
            kwargs["nextToken"] = token
        page = _mem().list_sessions(**kwargs)
        sessions.extend(page.get("sessionSummaries", []))
        token = page.get("nextToken")
        if not token:
            break
    out = []
    for s in sessions[:MAX_SESSIONS]:
        sid = s.get("sessionId")
        title = ""
        try:
            first = _session_events(user_id, sid, max_pages=1)
            title = _chat_title([m for e in first for m in _event_messages(e)])
        except Exception as e:  # noqa: BLE001 — a session without a title is still listed
            print("chat title lookup failed:", repr(e))
        out.append({"sessionId": sid, "createdAt": _iso(s.get("createdAt")), "title": title or "Conversation"})
    out.sort(key=lambda x: x["createdAt"], reverse=True)
    return _resp(event, 200, {"sessions": out})


def list_chats(event, user_id):
    sessions, unavailable = {}, False
    try:
        result = _memory_chats(event, user_id)
        sessions = {s["sessionId"]: s for s in json.loads(result["body"])["sessions"]}
    except Exception as exc:
        print("memory history unavailable:", type(exc).__name__)
        unavailable = True
    records = sorted(_user_records(user_id), key=lambda x: x.get("createdAt", ""))
    for item in records:
        sid = item.get("sessionId")
        if item.get("recordType") != "planning_run" or not sid:
            continue
        session = sessions.setdefault(sid, {"sessionId": sid, "createdAt": item["createdAt"],
                                            "title": item.get("title") or "Conversation"})
        session["run"] = _public_run(item)
        session["updatedAt"] = item["updatedAt"]
    out = sorted(sessions.values(), key=lambda x: x.get("updatedAt") or x.get("createdAt", ""), reverse=True)
    return _resp(event, 200, {"sessions": out[:MAX_SESSIONS], "memoryUnavailable": unavailable})


def get_chat(event, user_id, session_id):
    if not session_id or not SESSION_RE.fullmatch(session_id):
        return _error(event, 400, "bad_input", "invalid session id")
    request_id = (event.get("queryStringParameters") or {}).get("requestId")
    if request_id:
        if not SESSION_RE.fullmatch(request_id):
            return _error(event, 400, "bad_input", "invalid request id")
        run = _get_trip(user_id, "run_" + request_id)
        if not run or run.get("recordType") != "planning_run" or run.get("sessionId") != session_id:
            return _error(event, 404, "not_found", "request not found")
        runs, records, messages, unavailable = [run], [], [], False
    else:
        records = _user_records(user_id)
        runs = sorted([r for r in records if r.get("recordType") == "planning_run" and
                       r.get("sessionId") == session_id], key=lambda r: r["createdAt"])
        messages, unavailable = [], False
        try:
            messages = [m for e in _session_events(user_id, session_id) for m in _event_messages(e)]
        except Exception as exc:
            print("memory messages unavailable:", type(exc).__name__)
            unavailable = True
    receipt_messages = [m for r in runs for m in r.get("messages", [])]
    # Keep legacy messages, but don't repeat the same turn from Memory and receipts.
    remaining = Counter((m["role"].lower(), m["text"]) for m in receipt_messages)
    legacy = []
    for message in messages:
        key = (message["role"].lower(), message["text"])
        if remaining[key]:
            remaining[key] -= 1
        else:
            legacy.append(message)
    messages = legacy + receipt_messages
    if not messages and not runs:
        return _error(event, 404, "not_found", "chat not found")
    trip_ids = {r["resultTripId"] for r in runs if r.get("resultTripId")}
    trip_ids.update(r["tripId"] for r in records if r.get("recordType") != "planning_run" and
                    r.get("sessionId") == session_id)
    itineraries = []
    for trip_id in sorted(trip_ids):
        # Re-check ownership before resolving any pointer stored on a receipt.
        trip = _get_trip(user_id, trip_id)
        if trip and trip.get("recordType") != "planning_run":
            web = _web_itinerary(trip, _latest_itinerary(trip_id))
            if web:
                itineraries.append(web)
    return _resp(event, 200, {"sessionId": session_id, "messages": messages,
                             "itineraries": itineraries,
                             "run": _public_run(runs[-1]) if runs else None,
                             "memoryUnavailable": unavailable})


# ---------------------------------------------------------------- entry point
def lambda_handler(event, context):
    method = event.get("httpMethod", "")
    resource = event.get("resource", "")
    if method == "OPTIONS":
        return _resp(event, 204, {})

    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    user_id = claims.get("sub")
    if not user_id:
        return _error(event, 401, "unauthorized", "missing or invalid sign-in token")
    trip_id = (event.get("pathParameters") or {}).get("tripId")
    session_id = (event.get("pathParameters") or {}).get("sessionId")

    try:
        if resource == "/trips" and method == "GET":
            return list_trips(event, user_id)
        if resource == "/trips/{tripId}" and method == "GET":
            return get_trip(event, user_id, trip_id)
        if resource == "/trips/{tripId}/pdf" and method == "GET":
            return get_pdf(event, user_id, trip_id)
        if resource == "/trips/{tripId}/feedback" and method == "POST":
            return post_feedback(event, user_id, trip_id)
        if resource == "/me/preferences" and method == "GET":
            return get_prefs(event, user_id)
        if resource == "/me/preferences" and method == "PUT":
            return put_prefs(event, user_id)
        if resource == "/chats" and method == "GET":
            return list_chats(event, user_id)
        if resource == "/chats/{sessionId}" and method == "GET":
            return get_chat(event, user_id, session_id)
        return _error(event, 404, "no_route", f"{method} {resource} is not a known route")
    except KeyError as e:  # usually a missing environment variable
        print("config error:", repr(e))
        return _error(event, 500, "config", f"server is missing setting {e}")
    except Exception as e:  # noqa: BLE001 — log it, don't leak internals to the browser
        print("unhandled error:", repr(e))
        return _error(event, 500, "server_error", "something went wrong; check CloudWatch logs")
