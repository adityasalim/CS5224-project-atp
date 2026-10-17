"""
tp-render-pdf — Lambda tool: turn a final (verified) itinerary into a PDF travel plan with
clickable links, save it to S3 and return a short-lived download link.

Names, prices and links are read from the candidate pool by ID (not from model text).
Optional narrative text from the agent (already checked by Guardrails) can be included.

Environment variables:
  CACHE_TABLE    DynamoDB cache table holding pool:<id> items
  PLANS_BUCKET   S3 bucket for PDFs (e.g. tp-dev-plans-<suffix>)
  ITINERARIES_TABLE  optional (e.g. tp-dev-itineraries) — if set, the PDF's S3 key is recorded there
  DAILY_SPEND_SGD_PER_PERSON  default 100 (must match tp-verify-itinerary)
  LINK_EXPIRY_SECONDS         default 900 (15 min)

Input event:
  {"tripId": "trip_123", "version": 1, "title": "Tokyo honeymoon",
   "tripSpec": {...same as verify...}, "itinerary": {...same as verify, ideally sequence_day output...},
   "narrative": {"summary": "...", "dayNotes": {"2026-12-11": "..."}},
   "caveats": ["..."]}
"""
import json
import os
import time
from datetime import date, datetime
from decimal import Decimal

import boto3
from botocore.config import Config
from fpdf import FPDF  # from the tp-deps layer

_dynamodb = boto3.resource("dynamodb")
_REPLACE = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
            "…": "...", " ": " ", "•": "-", "→": "->"}


def _err(code, message, **extra):
    return {"ok": False, "error": code, "message": message, "source": "render-pdf", **extra}


def _t(s):
    """Built-in PDF fonts are Latin-1 only. Non-Latin text (e.g. Japanese) becomes '?'.
    To support it, add a Unicode TTF (e.g. Noto Sans JP) to the layer and use pdf.add_font()."""
    s = "" if s is None else str(s)
    for k, v in _REPLACE.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _pool_get(ids):
    table = os.environ.get("CACHE_TABLE")
    if not table or not ids:
        return {}
    keys = [{"cacheKey": "pool:" + i} for i in dict.fromkeys(ids)]
    found = {}
    for start in range(0, len(keys), 100):
        req = {table: {"Keys": keys[start:start + 100]}}
        for _ in range(5):
            resp = _dynamodb.batch_get_item(RequestItems=req)
            for item in resp["Responses"].get(table, []):
                data = json.loads(item["payload"])
                found[data["id"]] = data
            req = resp.get("UnprocessedKeys") or {}
            if not req:
                break
    return found


class PlanPDF(FPDF):
    def footer(self):
        self.set_y(-14)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 6, _t(f"Prices from provider data at planning time; this is a plan, not a booking.  "
                           f"Page {self.page_no()}"), align="C")


def build_pdf(event, pool, daily_spend):
    spec, it = event["tripSpec"], event["itinerary"]
    pax = int(spec.get("pax", 1))
    start, end = date.fromisoformat(spec["startDate"]), date.fromisoformat(spec["endDate"])
    n_days = (end - start).days + 1
    flight = pool.get(it.get("flightOfferId"))
    stay_refs = it.get("stays") or ([{"stayId": it["stayId"]}] if it.get("stayId") else [])
    stays = [pool[s_["stayId"]] for s_ in stay_refs if s_.get("stayId") in pool]
    transfers = [pool[t["transferId"]] for t in it.get("transfers") or [] if t.get("transferId") in pool]
    transfer_on = {t.get("date"): t for t in transfers}
    narrative = event.get("narrative") or {}

    pdf = PlanPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(18, 18, 18)
    pdf.add_page()
    accent = (180, 68, 47)

    # Title
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*accent)
    pdf.cell(0, 10, _t(event.get("title") or "Your travel plan"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6, _t(f"{start:%a %d %b %Y} - {end:%a %d %b %Y}  |  {n_days} days  |  {pax} traveller(s)"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    if narrative.get("summary"):
        pdf.set_text_color(30, 30, 30)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 5.5, _t(narrative["summary"]), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    # Costs (computed from pool prices, same formula as the verifier)
    flight_sgd = float(flight.get("priceTotalSGD") or 0) if flight else 0.0
    stay_sgd = sum(float(x.get("priceTotalSGD") or 0) for x in stays)
    transfer_sgd = sum(float(x.get("priceTotalSGD") or 0) for x in transfers)
    spend = daily_spend * pax * n_days
    total = flight_sgd + stay_sgd + transfer_sgd + spend
    budget = float(spec.get("budgetSGD", 0))

    def section(title):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(*accent)
        pdf.cell(0, 8, _t(title), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(*accent)
        pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
        pdf.ln(2)
        pdf.set_text_color(30, 30, 30)

    def cost_row(label, detail, amount, bold=False, link=None):
        pdf.set_font("Helvetica", "B" if bold else "", 10.5)
        pdf.cell(40, 6.5, _t(label))
        if link:
            pdf.set_text_color(30, 90, 170)
            pdf.cell(95, 6.5, _t(detail), link=link)
            pdf.set_text_color(30, 30, 30)
        else:
            pdf.cell(95, 6.5, _t(detail))
        pdf.cell(0, 6.5, _t(amount), align="R", new_x="LMARGIN", new_y="NEXT")

    section("Costs (SGD)")
    if flight:
        legs = " / ".join(f'{s.get("from")}->{s.get("to")} {str(s.get("departAt", ""))[:16].replace("T", " ")}'
                          for s in flight.get("slices", []))
        cost_row("Flights", f'{flight.get("airline", "")}  {legs}', f"{flight_sgd:,.2f}", link=flight.get("bookingUrl"))
    for st in stays:
        cost_row("Hotel (booked)" if st.get("booked") else "Hotel",
                 f'{st.get("name")} ({st.get("checkIn")} to {st.get("checkOut")})',
                 f'{float(st.get("priceTotalSGD") or 0):,.2f}', link=st.get("mapsUrl"))
    for t in transfers:
        label = "Transfer*" if t.get("estimate") else "Transfer"
        cost_row(label, f'{t.get("summary")} {str(t.get("departAt", ""))[:16].replace("T", " ")}',
                 f'{float(t.get("priceTotalSGD") or 0):,.2f}', link=t.get("bookingUrl"))
    cost_row("Daily spend", f"Estimate: SGD {daily_spend:g} x {pax} x {n_days} days", f"{spend:,.2f}")
    cost_row("Total", f"Budget SGD {budget:,.2f}  ({'within' if total <= budget else 'over'} budget)",
             f"{total:,.2f}", bold=True)
    if any(t.get("estimate") for t in transfers):
        pdf.set_font("Helvetica", "I", 8.5)
        pdf.set_text_color(110, 110, 110)
        pdf.multi_cell(0, 4.5, _t("* Train/bus/ferry prices and times are estimates from distance; use the link "
                                  "to check real timetables."), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(30, 30, 30)

    # Day by day
    section("Day by day")
    for i, d in enumerate(it.get("days") or []):
        dt = date.fromisoformat(d["date"])
        pdf.set_font("Helvetica", "B", 11.5)
        city = f' - {d["city"]}' if d.get("city") else ""
        pdf.cell(0, 7, _t(f"Day {i + 1} - {dt:%A %d %B}{city}"), new_x="LMARGIN", new_y="NEXT")
        t = transfer_on.get(d["date"])
        if t:
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(30, 90, 170)
            pdf.cell(0, 6, _t(f'{str(t.get("departAt", ""))[11:16]}-{str(t.get("arriveAt", ""))[11:16]}  '
                              f'{t.get("summary")}'), link=t.get("bookingUrl") or "", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(30, 30, 30)
        trip = d.get("dayTrip") if isinstance(d.get("dayTrip"), dict) else None
        if trip and trip.get("place"):
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_text_color(30, 90, 170)
            mins = trip.get("travelMinutes")
            how = f' ({trip.get("travelBy") or "train/bus"}, about {mins} min each way)' if mins else ""
            pdf.cell(0, 6, _t(f'Day trip to {trip["place"]}{how}, back to the hotel in the evening'),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(30, 30, 30)
        note = (narrative.get("dayNotes") or {}).get(d["date"])
        if note:
            pdf.set_font("Helvetica", "I", 10)
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(0, 5, _t(note), new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(30, 30, 30)
        stops = d.get("stops") or []
        if not stops and not t:
            pdf.set_font("Helvetica", "", 10)
            pdf.cell(0, 6, "Free day", new_x="LMARGIN", new_y="NEXT")
        for s in stops:
            p = pool.get(s.get("placeId"), {})
            name = p.get("name") or s.get("name") or s.get("placeId")
            when = f'{s.get("arriveAt", "")}-{s.get("departAt", "")}'
            travel = s.get("travelMinutesFromPrev")
            pdf.set_font("Helvetica", "", 10)
            pdf.set_text_color(110, 110, 110)
            pdf.cell(26, 6, _t(when))
            pdf.set_text_color(30, 90, 170)
            link = p.get("url") or p.get("mapsUrl")
            pdf.cell(110, 6, _t(name), link=link or "")
            pdf.set_text_color(110, 110, 110)
            extra = f'{travel} min {s.get("travelBy", "")}'.strip() if travel else ""
            pdf.cell(0, 6, _t(extra), align="R", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(30, 30, 30)
        pdf.ln(2)

    caveats = event.get("caveats") or []
    if caveats:
        section("Notes")
        pdf.set_font("Helvetica", "", 10)
        for c in caveats:
            pdf.multi_cell(0, 5.5, _t(f"- {c}"), new_x="LMARGIN", new_y="NEXT")

    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4.5, _t(f"Generated {datetime.utcnow():%d %b %Y %H:%M} UTC. Place data may include "
                              f"OpenStreetMap contributors' data. Check opening hours before you go."),
                   new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output()), round(total, 2)


def lambda_handler(event, context):
    if isinstance(event, str):
        event = json.loads(event)
    event = event or {}
    if not isinstance(event.get("tripSpec"), dict) or not isinstance(event.get("itinerary"), dict):
        return _err("bad_input", "need tripSpec and itinerary objects")
    bucket = os.environ.get("PLANS_BUCKET")
    if not bucket:
        return _err("config", "PLANS_BUCKET environment variable is not set")

    it = event["itinerary"]
    ids = [x for x in (it.get("flightOfferId"), it.get("stayId")) if x]
    ids += [s_.get("stayId") for s_ in it.get("stays") or [] if s_.get("stayId")]
    ids += [t.get("transferId") for t in it.get("transfers") or [] if t.get("transferId")]
    ids += [s.get("placeId") for d in it.get("days") or [] for s in d.get("stops") or [] if s.get("placeId")]
    pool = _pool_get(ids)
    try:
        pdf_bytes, total = build_pdf(event, pool, float(os.environ.get("DAILY_SPEND_SGD_PER_PERSON", "100")))
    except (KeyError, ValueError) as e:
        return _err("bad_input", f"could not build PDF: {e}")

    trip_id = str(event.get("tripId") or f"trip_{int(time.time())}")
    key = f"plans/{trip_id}/{datetime.utcnow():%Y%m%dT%H%M%S}.pdf"
    region = os.environ.get("AWS_REGION", "ap-southeast-1")
    s3 = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com",
                      config=Config(signature_version="s3v4"))
    s3.put_object(Bucket=bucket, Key=key, Body=pdf_bytes, ContentType="application/pdf",
                  ContentDisposition=f'inline; filename="{trip_id}.pdf"')
    expires = int(os.environ.get("LINK_EXPIRY_SECONDS", "900"))
    url = s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)

    table = os.environ.get("ITINERARIES_TABLE")
    if table and event.get("tripId"):
        try:
            _dynamodb.Table(table).update_item(
                Key={"tripId": trip_id, "version": str(event.get("version", "1"))},
                UpdateExpression="SET pdfKey = :k, pdfCreatedAt = :t, totalSGD = :s",
                ExpressionAttributeValues={":k": key, ":t": datetime.utcnow().isoformat() + "Z",
                                           ":s": Decimal(str(total))})
        except Exception as e:  # noqa: BLE001 — the PDF exists; don't fail the tool over bookkeeping
            print("could not record pdfKey:", e)

    return {"ok": True, "s3Key": key, "url": url, "expiresInSeconds": expires,
            "sizeBytes": len(pdf_bytes), "totalSGD": total}
