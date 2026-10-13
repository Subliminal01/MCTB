import os

from twilio.rest import Client


def send_staff_notifications(clinic, caller: str, body: str) -> None:
    message = f"SMS reply from {caller}: {body}"
    if clinic["notify_sms"] and os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN"):
        Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]).messages.create(
            from_=clinic["twilio_number"], to=clinic["notify_sms"], body=message[:1500])
