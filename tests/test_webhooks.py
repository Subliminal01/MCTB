import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from starlette.requests import Request

from app import db, main


def fake_request(path: str, query: bytes = b"") -> Request:
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": query, "root_path": "",
        "headers": [], "client": ("127.0.0.1", 12345), "server": ("testserver", 80),
    })


def run_immediate(coroutine):
    """Run an async route whose patched dependencies complete without I/O."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    raise AssertionError("Route unexpectedly yielded; this offline test must not perform I/O")


class WebhookFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.path.dirname(__file__))
        self.env = patch.dict(os.environ, {
            "DATABASE_PATH": os.path.join(self.temp.name, "webhooks.sqlite3"),
            "TWILIO_ACCOUNT_SID": "AC00000000000000000000000000000000",
            "TWILIO_AUTH_TOKEN": "test-token",
        })
        self.env.start()
        db.init_db()
        with db.connect() as conn:
            self.clinic_id = conn.execute("""INSERT INTO clinics(name,twilio_number,forward_to_number,
                booking_link,sms_template) VALUES('Harbour Clinic','+61280000000','+61280000001',
                'https://example.test/book','Hi from {clinic_name}: {booking_link}')""").lastrowid

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_missed_call_sends_once_and_records_roi(self):
        form = {"To": "+61280000000", "From": "+61412345678", "DialCallStatus": "no-answer"}
        async def validated_form(_request):
            return form

        with patch("app.main.validate_twilio", new=validated_form), \
             patch("app.main.Client") as twilio:
            run_immediate(main.voice_status(fake_request("/voice/status")))
            run_immediate(main.voice_status(fake_request("/voice/status")))

        self.assertEqual(twilio.return_value.messages.create.call_count, 1)
        sent_body = twilio.return_value.messages.create.call_args.kwargs["body"]
        self.assertIn("Harbour Clinic", sent_body)
        self.assertIn("https://example.test/book", sent_body)
        self.assertIn("Reply STOP to opt out", sent_body)
        result = db.report(self.clinic_id, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
        self.assertEqual(result["total_missed_calls"], 2)
        self.assertEqual(result["texts_sent"], 1)

    def test_stop_reply_records_optout_and_suppresses_future_text(self):
        stop = {"To": "+61280000000", "From": "+61412345678", "Body": "STOP"}
        async def validated_stop(_request):
            return stop

        with patch("app.main.validate_twilio", new=validated_stop):
            response = run_immediate(main.sms_incoming(fake_request("/sms/incoming")))
        self.assertIn("<Response", response.body.decode())
        self.assertTrue(db.opted_out(self.clinic_id, "+61412345678"))

        missed = {"To": "+61280000000", "From": "+61412345678", "DialCallStatus": "busy"}
        async def validated_missed(_request):
            return missed

        with patch("app.main.validate_twilio", new=validated_missed), \
             patch("app.main.Client") as twilio:
            run_immediate(main.voice_status(fake_request("/voice/status")))
        twilio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
