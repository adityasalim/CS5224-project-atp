"""Partner agency and package management API; requires a verified Cognito authorizer."""
import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError
from marketplace_domain import validate_agency, validate_package, validate_transition, require_owner, ValidationError

_dynamo = boto3.resource("dynamodb")

class Encoder(json.JSONEncoder):
    def default(self, value):
        if isinstance(value, Decimal):
            return str(value)
        return super().default(value)

def response(event, status, data):
    headers = {"Content-Type": "application/json", "Vary": "Origin"}
    origin = (event.get("headers") or {}).get("origin") or (event.get("headers") or {}).get("Origin")
    allowed = [s.strip() for s in os.environ.get("ALLOWED_ORIGINS", "").split(",") if s.strip()]
    if origin and origin in allowed:
        headers.update({"Access-Control-Allow-Origin": origin, "Access-Control-Allow-Headers": "Authorization,Content-Type",
                        "Access-Control-Allow-Methods": "GET,POST,PUT,OPTIONS"})
    return {"statusCode": status, "headers": headers, "body": json.dumps(data, cls=Encoder)}

def now():
    return datetime.now(timezone.utc).isoformat()

def agency_id(sub):
    return "agency_" + hashlib.sha256(sub.encode("utf-8")).hexdigest()

def claims_for(event):
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    return authorizer.get("claims") or (authorizer.get("jwt") or {}).get("claims") or {}

def is_admin(claims):
    raw = claims.get("cognito:groups", [])
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        raw = parsed if isinstance(parsed, list) else [x.strip() for x in raw.strip("[]").split(",")]
    return isinstance(raw, list) and os.environ.get("ADMIN_GROUP", "platform_admin") in raw

def body_for(event):
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw, validate=True).decode("utf-8")
    if not isinstance(raw, str) or len(raw) > 100000:
        raise ValidationError("invalid body")
    try:
        body = json.loads(raw, parse_float=Decimal)
    except (ValueError, TypeError) as exc:
        raise ValidationError("invalid JSON") from exc
    if not isinstance(body, dict):
        raise ValidationError("body must be an object")
    return body

def get_agency(table, identifier):
    return table.get_item(Key={"agencyId": identifier}, ConsistentRead=True).get("Item")

def scan_status(table, status):
    # Bounded response: caller can request subsequent pages via an explicit cursor in future.
    result = table.scan(FilterExpression="#s = :s", ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":s": status}, Limit=100)
    return result.get("Items", []), bool(result.get("LastEvaluatedKey"))

def atomic_package_write(agency_id_value, operation, owner_sub=None):
    """Apply a package write only while its agency remains approved."""
    serializer = TypeSerializer()
    encode = lambda values: {k: serializer.serialize(v) for k, v in values.items()}
    check = {"TableName": os.environ["AGENCIES_TABLE"],
             "Key": encode({"agencyId": agency_id_value}),
             "ConditionExpression": "#status = :approved",
             "ExpressionAttributeNames": {"#status": "status"},
             "ExpressionAttributeValues": encode({":approved": "APPROVED"})}
    if owner_sub is not None:
        check["ConditionExpression"] += " AND ownerSub = :owner"
        check["ExpressionAttributeValues"].update(encode({":owner": owner_sub}))
    operation_type, body = next(iter(operation.items()))
    body = dict(body)
    for field in ("Key", "Item", "ExpressionAttributeValues"):
        if field in body:
            body[field] = encode(body[field])
    boto3.client("dynamodb").transact_write_items(TransactItems=[
        {"ConditionCheck": check}, {operation_type: body}])

def lambda_handler(event, context):
    method = event.get("httpMethod") or (event.get("requestContext") or {}).get("http", {}).get("method", "")
    if method == "OPTIONS":
        return response(event, 204, {})
    claims = claims_for(event)
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        return response(event, 401, {"error": "unauthorized"})
    route = event.get("resource") or event.get("routeKey", "").split(" ", 1)[-1]
    path = event.get("pathParameters") or {}
    admin = is_admin(claims)
    try:
        agencies = _dynamo.Table(os.environ["AGENCIES_TABLE"])
        packages = _dynamo.Table(os.environ["PACKAGES_TABLE"])
        own_id = agency_id(sub)
        if route == "/agency/register" and method == "POST":
            data = validate_agency(body_for(event))
            item = dict(data, agencyId=own_id, ownerSub=sub, status="PENDING_REVIEW", createdAt=now(), updatedAt=now())
            agencies.put_item(Item=item, ConditionExpression="attribute_not_exists(agencyId)")
            return response(event, 201, {"agencyId": own_id, "status": "PENDING_REVIEW"})
        if route.startswith("/admin/"):
            if not admin:
                raise PermissionError("administrator required")
            if route == "/admin/agencies/pending" and method == "GET":
                items, more = scan_status(agencies, "PENDING_REVIEW")
                return response(event, 200, {"agencies": items, "truncated": more})
            if route == "/admin/packages/pending" and method == "GET":
                items, more = scan_status(packages, "PENDING_REVIEW")
                visible=[]
                for item in items:
                    owner=get_agency(agencies,item.get('agencyId'))
                    if owner and owner.get('status') in ('APPROVED','PENDING_REVIEW'):
                        visible.append(dict(item,providerName=owner.get('tradingName',''),providerStatus=owner['status']))
                return response(event, 200, {"packages": visible, "truncated": more})
            if route in ("/admin/packages/{packageId}/approve", "/admin/packages/{packageId}/reject") and method == "POST":
                admin_body = body_for(event)
                agency_identifier = admin_body.get("agencyId")
                if not isinstance(agency_identifier, str) or not agency_identifier:
                    raise ValidationError("agencyId required")
                agency = get_agency(agencies, agency_identifier)
                key = {"agencyId": agency_identifier, "packageId": path.get("packageId", "")}
                item = packages.get_item(Key=key, ConsistentRead=True).get("Item")
                if not agency or not item:
                    return response(event, 404, {"error": "not_found"})
                target = "PUBLISHED" if route.endswith("/approve") else "DRAFT"
                validate_transition("package", item["status"], target, "admin", agency["status"])
                if target == "PUBLISHED":
                    validate_package(item)
                atomic_package_write(agency_identifier, {"Update": {"TableName": os.environ["PACKAGES_TABLE"], "Key": key,
                    "UpdateExpression": "SET #s=:new, updatedAt=:now", "ConditionExpression": "#s=:old AND updatedAt=:previous",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":new": target, ":old": item["status"], ":now": now(),
                                                   ":previous": item["updatedAt"]}}})
                return response(event, 200, {"status": target})
            identifier = path.get("agencyId")
            if route in ("/admin/agencies/{agencyId}/approve", "/admin/agencies/{agencyId}/reject", "/admin/agencies/{agencyId}/suspend") and method == "POST":
                agency = get_agency(agencies, identifier)
                if not agency:
                    return response(event, 404, {"error": "not_found"})
                target = {"approve":"APPROVED", "reject":"REJECTED", "suspend":"SUSPENDED"}[route.rsplit("/",1)[1]]
                validate_transition("agency", agency["status"], target, "admin", agency["status"])
                agencies.update_item(Key={"agencyId": identifier}, UpdateExpression="SET #s=:new, updatedAt=:now",
                    ConditionExpression="#s=:old", ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":new": target, ":old": agency["status"], ":now": now()})
                return response(event, 200, {"status": target})
            return response(event, 404, {"error": "not_found"})
        agency = get_agency(agencies, own_id)
        if not agency:
            return response(event, 404, {"error": "not_registered"})
        require_owner(sub, agency)
        if route == "/agency/me" and method == "GET":
            return response(event, 200, {"agency": agency})
        if route == "/agency/me" and method == "PUT":
            data = validate_agency(body_for(event))
            updated = dict(agency, **data, updatedAt=now())
            agencies.put_item(Item=updated, ConditionExpression="ownerSub=:owner AND #s=:s AND updatedAt=:previous",
                ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":owner": sub, ":s": agency["status"], ":previous": agency["updatedAt"]})
            return response(event, 200, {"agency": updated})
        if agency["status"] != "APPROVED":
            raise PermissionError("approved agency required")
        if route == "/agency/packages" and method == "GET":
            items = packages.query(KeyConditionExpression=Key("agencyId").eq(own_id), Limit=100)
            return response(event, 200, {"packages": items.get("Items", []), "truncated": bool(items.get("LastEvaluatedKey"))})
        if route == "/agency/packages" and method == "POST":
            data = validate_package(body_for(event))
            item = dict(data, agencyId=own_id, packageId="pkg_"+uuid.uuid4().hex, status="DRAFT", createdAt=now(), updatedAt=now())
            atomic_package_write(own_id, {"Put": {"TableName": os.environ["PACKAGES_TABLE"], "Item": item,
                "ConditionExpression": "attribute_not_exists(packageId)"}}, sub)
            return response(event, 201, {"package": item})
        package_id = path.get("packageId")
        if package_id and route.startswith("/agency/packages/{packageId}"):
            key = {"agencyId": own_id, "packageId": package_id}
            item = packages.get_item(Key=key, ConsistentRead=True).get("Item")
            if not item:
                return response(event, 404, {"error": "not_found"})
            if route == "/agency/packages/{packageId}" and method == "GET":
                return response(event, 200, {"package": item})
            if route == "/agency/packages/{packageId}" and method == "PUT":
                if item["status"] != "DRAFT":
                    raise ValidationError("only drafts can be edited")
                data = validate_package(body_for(event))
                replacement = dict(data, agencyId=own_id, packageId=package_id, status="DRAFT",
                                   createdAt=item["createdAt"], updatedAt=now())
                atomic_package_write(own_id, {"Put": {"TableName": os.environ["PACKAGES_TABLE"], "Item": replacement,
                    "ConditionExpression": "#s=:old AND updatedAt=:previous",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":old": "DRAFT", ":previous": item["updatedAt"]}}}, sub)
                return response(event, 200, {"package": replacement})
            actions = {"submit":"PENDING_REVIEW", "pause":"PAUSED", "withdraw":"WITHDRAWN"}
            action = route.rsplit("/", 1)[-1]
            if method == "POST" and action in actions:
                target = validate_transition("package", item["status"], actions[action], "agency", agency["status"])
                atomic_package_write(own_id, {"Update": {"TableName": os.environ["PACKAGES_TABLE"], "Key": key,
                    "UpdateExpression": "SET #s=:new, updatedAt=:now", "ConditionExpression": "#s=:old AND updatedAt=:previous",
                    "ExpressionAttributeNames": {"#s": "status"},
                    "ExpressionAttributeValues": {":new": target, ":old": item["status"], ":now": now(),
                                                   ":previous": item["updatedAt"]}}}, sub)
                return response(event, 200, {"status": target})
        return response(event, 404, {"error": "not_found"})
    except PermissionError:
        return response(event, 403, {"error": "forbidden"})
    except (ValidationError, ValueError, TypeError):
        return response(event, 400, {"error": "invalid_request"})
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        reasons = exc.response.get("CancellationReasons") or []
        if code == "ConditionalCheckFailedException" or (
                code == "TransactionCanceledException" and
                any(reason.get("Code") == "ConditionalCheckFailed" for reason in reasons)):
            return response(event, 409, {"error": "conflict"})
        return response(event, 500, {"error": "server_error"})
    except Exception:
        return response(event, 500, {"error": "server_error"})
