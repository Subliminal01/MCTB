import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from twilio.twiml.messaging_response import MessagingResponse

from . import db
from .notifications import send_staff_notifications
from .rules import is_missed_call_status, is_stop_keyword, valid_caller_number
from .schedule import should_text

app = FastAPI(title="Missed-Call Text-Back", version="1.0.0")
DEFAULT_TEMPLATE = "Hi from {clinic_name}! Sorry we missed your call. Looking to book a consultation or check availability? Reply here or pick a time: {booking_link}. Reply STOP to opt out."


@app.on_event("startup")
def startup():
    db.init_db()


def public_url(request: Request) -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if base:
        return base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return str(request.url)


async def validate_twilio(request: Request) -> dict:
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    signature = request.headers.get("X-Twilio-Signature", "")
    if not auth_token or not signature:
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    form = await request.form()
    params = dict(form)
    if not RequestValidator(auth_token).validate(public_url(request), params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    return params


def admin_guard(x_admin_key: str | None = Header(default=None)):
    key = os.getenv("ADMIN_API_KEY")
    if not key or not x_admin_key or not __import__("hmac").compare_digest(key, x_admin_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/voice/incoming")
async def voice_incoming(request: Request):
    form = await validate_twilio(request)
    clinic = db.clinic_by_number(form.get("To", ""))
    response = VoiceResponse()
    if clinic:
        callback_base = os.getenv("PUBLIC_BASE_URL", "").rstrip("/") or str(request.base_url).rstrip("/")
        callback = f"{callback_base}/voice/status?clinic_id={clinic['id']}"
        response.dial(clinic["forward_to_number"], timeout=20, action=callback, method="POST")
    return Response(content=str(response), media_type="application/xml")


@app.post("/voice/status")
async def voice_status(request: Request):
    form = await validate_twilio(request)
    status = (form.get("DialCallStatus") or "").lower()
    caller = form.get("From", "")
    clinic = db.clinic_by_number(form.get("To", ""))
    if not clinic:
        clinic_id = request.query_params.get("clinic_id")
        if clinic_id and clinic_id.isdigit():
            with db.connect() as conn:
                clinic = conn.execute("SELECT * FROM clinics WHERE id=? AND active=1", (int(clinic_id),)).fetchone()
    if clinic and is_missed_call_status(status):
        now = datetime.now(timezone.utc)
        db.add_event(clinic["id"], caller, "missed_call")
        if (valid_caller_number(caller) and not db.opted_out(clinic["id"], caller)
                and should_text(clinic, now)):
            template = clinic["sms_template"] or DEFAULT_TEMPLATE
            body = template.format(clinic_name=clinic["name"], booking_link=clinic["booking_link"])
            if not any(word in body.upper() for word in ("STOP", "UNSUBSCRIBE")):
                body = f"{body} Reply STOP to opt out."
            sid, token = os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN")
            if sid and token:
                reservation = db.claim_sms_slot(clinic["id"], caller,
                    (now - timedelta(hours=6)).isoformat(), body)
                if reservation is not None:
                    try:
                        Client(sid, token).messages.create(from_=clinic["twilio_number"], to=caller, body=body)
                    except Exception:
                        db.release_sms_slot(reservation)
                        raise
    return Response(content=str(VoiceResponse()), media_type="application/xml")


@app.post("/sms/incoming")
async def sms_incoming(request: Request):
    form = await validate_twilio(request)
    caller, body = form.get("From", ""), form.get("Body", "")
    clinic = db.clinic_by_number(form.get("To", ""))
    if clinic:
        db.add_event(clinic["id"], caller, "reply", body)
        if is_stop_keyword(body):
            db.add_optout(clinic["id"], caller)
            db.add_event(clinic["id"], caller, "opt_out", body)
        else:
            try:
                send_staff_notifications(clinic, caller, body)
            except Exception:
                # Keep Twilio's webhook successful even if staff notification delivery fails.
                pass
    return Response(content=str(MessagingResponse()), media_type="application/xml")


@app.get("/admin/report", dependencies=[Depends(admin_guard)])
def admin_report(clinic_id: int, days: int = 30):
    if days < 1 or days > 365:
        raise HTTPException(status_code=422, detail="days must be between 1 and 365")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return db.report(clinic_id, since)
