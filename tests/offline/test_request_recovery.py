"""Exercise persistence, reconnects, ownership and legacy PDF recovery without AWS."""
import ast
import copy
import importlib.util
import json
import os
import pathlib
import re
import time
import tempfile
import types
import unittest
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_history", ROOT / "agent/run_history.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
RunHistory = module.RunHistory


class ConditionalConflict(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class Key:
    def __init__(self, name):
        self.name = name

    def eq(self, value):
        return self.name, value


class Table:
    def __init__(self):
        self.rows = {}

    def key(self, item):
        return (item.get("userId", item.get("tripId")), item.get("tripId") if "userId" in item else item.get("version"))

    def put_item(self, Item, ConditionExpression=None):
        key = self.key(Item)
        if ConditionExpression and key in self.rows:
            raise ConditionalConflict()
        self.rows[key] = copy.deepcopy(Item)

    def get_item(self, Key, **kwargs):
        return {"Item": copy.deepcopy(self.rows.get(self.key(Key)))}

    def query(self, KeyConditionExpression, ExclusiveStartKey=None, **kwargs):
        field, value = KeyConditionExpression
        rows = [r for r in self.rows.values() if r.get(field) == value]
        offset = (ExclusiveStartKey or {}).get("offset", 0)
        # One result per page forces callers to handle receipt/version pagination.
        result = {"Items": copy.deepcopy(rows[offset:offset + 1])}
        if offset + 1 < len(rows):
            result["LastEvaluatedKey"] = {"offset": offset + 1}
        return result


def definitions(path, names=None):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and
             (names is None or n.name in names)]
    for node in nodes:
        node.decorator_list = []
    return compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec")


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.trips, self.itineraries = Table(), Table()
        self.api = dict(json=json, re=re, os=os, Counter=Counter, datetime=datetime, timezone=timezone,
                        Decimal=Decimal, Key=Key, MAX_EVENT_PAGES=5, SESSION_RE=re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$"))
        exec(definitions(ROOT / "lambdas/tp-api-trips/lambda_function.py"), self.api)
        self.api["_table"] = lambda name: self.trips if name == "TRIPS_TABLE" else self.itineraries
        self.api["_session_events"] = lambda *args, **kwargs: []

    def call(self, name, *args):
        result = self.api[name](*args)
        return result["statusCode"], json.loads(result["body"])

    def test_receipt_survives_disconnect_and_duplicate_never_claims_again(self):
        run = RunHistory(self.trips, "alice", "session-1", "request-1")
        self.assertTrue(run.begin("Plan my trip"))
        run.checkpoint(status="interrupted", progress="Searching hotels")
        duplicate = RunHistory(self.trips, "alice", "session-1", "request-1")
        self.assertFalse(duplicate.begin("Plan my trip"))
        self.assertEqual(duplicate.public()["status"], "interrupted")
        with self.assertRaises(ValueError):
            RunHistory(self.trips, "alice", "other-session", "request-1").begin("Another request")
        self.assertTrue(RunHistory(self.trips, "bob", "session-1", "request-1").begin("Bob's trip"))
        duplicate.item["version"] = Decimal("2")
        self.assertEqual(json.loads(json.dumps(duplicate.public()))["version"], 2)

    def test_request_lookup_checks_user_and_session(self):
        RunHistory(self.trips, "alice", "session-1", "request-1").begin("Plan")
        event = {"queryStringParameters": {"requestId": "request-1"}}
        status, body = self.call("get_chat", event, "alice", "session-1")
        self.assertEqual(status, 200)
        self.assertEqual(body["run"]["status"], "planning")
        self.assertEqual(self.call("get_chat", event, "bob", "session-1")[0], 404)
        self.assertEqual(self.call("get_chat", event, "alice", "other-session")[0], 404)

    def test_failed_request_is_listed_when_memory_is_unavailable(self):
        run = RunHistory(self.trips, "alice", "session-1", "request-1")
        run.begin("Plan"); run.checkpoint(status="failed", message="Tool unavailable")
        self.api["_memory_chats"] = lambda *args: (_ for _ in ()).throw(RuntimeError("memory offline"))
        self.api["MAX_SESSIONS"] = 50
        status, body = self.call("list_chats", {}, "alice")
        self.assertEqual(status, 200)
        self.assertTrue(body["memoryUnavailable"])
        self.assertEqual(body["sessions"][0]["run"]["status"], "failed")

    def test_trip_listing_skips_receipts_across_pages(self):
        RunHistory(self.trips, "alice", "session-1", "request-1").begin("Plan")
        self.trips.put_item(Item={"userId": "alice", "tripId": "trip-1", "createdAt": "2026-01-01"})
        self.trips.put_item(Item={"userId": "bob", "tripId": "secret-trip"})
        status, body = self.call("list_trips", {}, "alice")
        self.assertEqual(status, 200)
        self.assertEqual([t["tripId"] for t in body["trips"]], ["trip-1"])

    def test_restores_full_card_without_expired_pdf_url(self):
        run = RunHistory(self.trips, "alice", "session-1", "request-1"); run.begin("Plan")
        run.checkpoint(status="completed", itinerary={"tripId": "trip-1", "version": 2})
        self.trips.put_item(Item={"userId": "alice", "tripId": "trip-1", "sessionId": "session-1"})
        for version in ("2", "10"):
            self.itineraries.put_item(Item={"tripId": "trip-1", "version": version, "pdfKey": "plans/final.pdf",
                "webItinerary": json.dumps({"title": "Saved Tokyo trip", "days": [{"title": "Day 1"}], "pdfUrl": "https://expired.test"})})
        status, body = self.call("get_chat", {"queryStringParameters": {"requestId": "request-1"}}, "alice", "session-1")
        self.assertEqual(status, 200)
        card = body["itineraries"][0]
        self.assertEqual(card["version"], 10)
        self.assertEqual(card["days"][0]["title"], "Day 1")
        self.assertTrue(card["hasPdf"])
        self.assertNotIn("pdfUrl", card)

    def test_legacy_pdf_and_owner_check(self):
        self.trips.put_item(Item={"userId": "alice", "tripId": "legacy", "title": "Old trip"})
        self.itineraries.put_item(Item={"tripId": "legacy", "version": "1", "pdfKey": "old.pdf", "narrative": '{"summary":"Old summary"}'})
        self.api["_presign"] = lambda key: "https://fresh.test/" + key
        status, body = self.call("get_trip", {}, "alice", "legacy")
        self.assertEqual(status, 200)
        self.assertEqual(body["webItinerary"]["summary"], "Old summary")
        self.assertTrue(body["webItinerary"]["hasPdf"])
        self.assertEqual(self.call("get_pdf", {}, "alice", "legacy")[1]["url"], "https://fresh.test/old.pdf")
        self.assertEqual(self.call("get_pdf", {}, "bob", "legacy")[0], 404)

    def invoke(self, generator):
        ns = dict(os=os, re=re, uuid=uuid, traceback=types.SimpleNamespace(print_exc=lambda: None),
                  RunHistory=RunHistory, SESSIONS={}, REGION="test", PACKAGE_MODES={"compare"},
                  GatewayError=type("GatewayError", (Exception,), {}),
                  _user_from_context=lambda *args: "alice", _session_key=lambda u, s: (u, s),
                  _memory_session_id=lambda s: s, _touch_session=lambda *args: None,
                  memory_save=lambda *args: None, chat_turn=generator,
                  boto3=types.SimpleNamespace(resource=lambda *args, **kwargs: types.SimpleNamespace(Table=lambda _: self.trips)),
                  ev=lambda kind, message=None, **extra: {"type": kind, **({"message": message} if message else {}), **extra},
                  MAX_PROMPT_CHARS=4000)
        exec(definitions(ROOT / "agent/tp_travel_agent.py", {"invoke"}), ns)
        os.environ["TRIPS_TABLE"] = "trips"
        return ns["invoke"], types.SimpleNamespace(session_id="session-1")

    def test_generator_close_and_duplicate_do_not_rerun_models(self):
        calls = []
        def planning(*args):
            calls.append(args)
            yield {"type": "status", "message": "Working"}
        invoke, context = self.invoke(planning)
        stream = invoke({"prompt": "Plan", "requestId": "request-1"}, context)
        self.assertEqual(next(stream)["type"], "run")
        self.assertEqual(next(stream)["type"], "status")
        stream.close()
        self.assertEqual(self.trips.get_item(Key={"userId": "alice", "tripId": "run_request-1"})["Item"]["status"], "interrupted")
        replay = list(invoke({"prompt": "Plan", "requestId": "request-1"}, context))
        self.assertTrue(replay[0]["duplicate"])
        self.assertEqual(len(calls), 1)

    def test_generator_failure_keeps_original_request(self):
        def failed(*args):
            yield {"type": "status", "message": "Looking up flights"}
            raise RuntimeError("connection reset")
        invoke, context = self.invoke(failed)
        events = list(invoke({"prompt": "Plan", "requestId": "request-1"}, context))
        self.assertEqual(events[-1]["code"], "PLANNING_FAILED")
        item = self.trips.get_item(Key={"userId": "alice", "tripId": "run_request-1"})["Item"]
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["messages"][0]["text"], "Plan")

    def test_completed_itinerary_survives_close_before_done(self):
        def completed(*args):
            yield {"type": "itinerary", "itinerary": {"tripId": "trip-1", "version": 1, "historySaved": True}}
        invoke, context = self.invoke(completed)
        stream = invoke({"prompt": "Plan", "requestId": "request-1"}, context)
        next(stream)
        self.assertEqual(next(stream)["type"], "itinerary")
        stream.close()
        run = self.trips.get_item(Key={"userId": "alice", "tripId": "run_request-1"})["Item"]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["resultTripId"], "trip-1")

    def test_no_models_run_when_receipt_cannot_be_saved(self):
        calls = []
        def planning(*args):
            calls.append(args)
            yield {"type": "status", "message": "Working"}
        invoke, context = self.invoke(planning)
        self.trips.put_item = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("write denied"))
        events = list(invoke({"prompt": "Plan", "requestId": "request-1"}, context))
        self.assertEqual(events[0]["code"], "HISTORY_UNAVAILABLE")
        self.assertEqual(calls, [])


class DeploymentTests(unittest.TestCase):
    def deploy(self, settings):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            (root / "web/agency").mkdir(parents=True)
            for path in ("index.html", "auth-session.js", "agency/config.js", "agency/index.html"):
                (root / "web" / path).write_text("fixture")
            uploads = []
            s3 = types.SimpleNamespace(put_object=lambda **kwargs: uploads.append(kwargs))
            ns = dict(REPO=root, DRY=False, json=json, time=time,
                      env=lambda key: settings.get(key, ""), touched=lambda *args: True,
                      fill_config=lambda html: (html, []),
                      aws=lambda _: s3)
            exec(definitions(ROOT / "scripts/deploy.py", {"deploy_web"}), ns)
            ns["deploy_web"]({"web/auth-session.js"}, "commit", [])
            return uploads

    def test_assets_precede_root_and_manual_provider_config_survives(self):
        uploads = self.deploy({"WEB_BUCKET": "test-bucket"})
        keys = [item["Key"] for item in uploads]
        self.assertIn("auth-session.js", keys)
        self.assertIn("agency/index.html", keys)
        self.assertNotIn("agency/config.js", keys)
        self.assertEqual(keys[-1], "index.html")

    def test_explicit_provider_configuration_is_uploaded(self):
        uploads = self.deploy({"WEB_BUCKET": "test-bucket", "WEB_AGENCY_API_URL": "https://api.test/dev",
                               "WEB_AGENCY_TERMS_URL": "https://site.test/terms", "WEB_ADMIN_GROUP": "admins"})
        config = next(item for item in uploads if item["Key"] == "agency/config.js")
        self.assertIn(b'https://api.test/dev', config["Body"])
        self.assertIn(b'admins', config["Body"])


if __name__ == "__main__":
    unittest.main()
