"""Durable request receipts in the existing user-scoped trips table.

Receipts deliberately have a different recordType from finished trips. A request
ID is claimed once: reconnecting must check its status, never run it twice.
"""
import time
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RunHistory:
    def __init__(self, table, user_id, session_id, request_id):
        self.table = table
        self.key = {"userId": user_id, "tripId": "run_" + request_id}
        self.session_id = session_id
        self.request_id = request_id
        self.item = None
        self.last_checkpoint = 0

    def begin(self, prompt):
        timestamp = utcnow()
        item = {**self.key, "recordType": "planning_run", "sessionId": self.session_id,
                "requestId": self.request_id, "title": prompt[:80], "status": "planning",
                "createdAt": timestamp, "updatedAt": timestamp, "progress": "Request received",
                "messages": [{"role": "user", "text": prompt[:4000], "at": timestamp}]}
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(tripId)")
        except Exception as exc:
            if getattr(exc, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
                raise
            self.item = self.table.get_item(Key=self.key, ConsistentRead=True).get("Item")
            if not self.item or self.item.get("sessionId") != self.session_id:
                raise ValueError("request ID already belongs to another conversation") from exc
            return False
        self.item = item
        self.last_checkpoint = time.monotonic()
        return True

    def checkpoint(self, status=None, progress=None, message=None, itinerary=None, force=False, choices=None):
        if choices is not None:
            self.item["choices"] = list(choices)[:20]
        if progress:
            self.item["progress"] = str(progress)[:500]
        if message:
            self.item["messages"].append({"role": "assistant", "text": str(message)[:4000], "at": utcnow()})
            self.item["messages"] = self.item["messages"][-30:]
        if itinerary:
            self.item.update(resultTripId=itinerary["tripId"], version=itinerary.get("version", 1))
        if status:
            self.item["status"] = status
        if not (force or status or message or itinerary) and time.monotonic() - self.last_checkpoint < 15:
            return
        self.item["updatedAt"] = utcnow()
        # The conditional claim above is the only writer for this request ID.
        self.table.put_item(Item=self.item)
        self.last_checkpoint = time.monotonic()

    def public(self):
        result = {key: self.item[key] for key in
                  ("requestId", "sessionId", "status", "progress", "updatedAt", "resultTripId", "version", "choices")
                  if key in self.item}
        if "version" in result:
            result["version"] = int(result["version"])  # DynamoDB reads numbers as Decimal.
        return result
