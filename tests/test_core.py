import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import db
from app.rules import STOP_WORDS, is_missed_call_status, is_stop_keyword, valid_caller_number


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.path.dirname(__file__))
        self.env = patch.dict(os.environ, {"DATABASE_PATH": os.path.join(self.temp.name, "test.sqlite3")})
        self.env.start()
        db.init_db()
        with db.connect() as conn:
            self.clinic_id = conn.execute("""INSERT INTO clinics(name,twilio_number,forward_to_number,sms_template)
                VALUES('Harbour Clinic','+61280000000','+61280000001','Hi {clinic_name}: {booking_link}')""").lastrowid

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_missed_call_and_rate_limit_window(self):
        now = datetime.now(timezone.utc)
        db.add_event(self.clinic_id, "+61400000000", "missed_call", timestamp=now.isoformat())
        self.assertFalse(db.recently_texted(self.clinic_id, "+61400000000", (now - timedelta(hours=6)).isoformat()))
        db.add_event(self.clinic_id, "+61400000000", "sms_sent", "callback", timestamp=now.isoformat())
        self.assertTrue(db.recently_texted(self.clinic_id, "+61400000000", (now - timedelta(hours=6)).isoformat()))
        self.assertFalse(db.recently_texted(self.clinic_id, "+61400000000", (now + timedelta(seconds=1)).isoformat()))

    def test_opt_out_is_per_clinic_and_stop_keywords_are_recognized(self):
        db.add_optout(self.clinic_id, "+61400000000")
        self.assertTrue(db.opted_out(self.clinic_id, "+61400000000"))
        self.assertFalse(db.opted_out(self.clinic_id + 1, "+61400000000"))
        self.assertIn("STOP", STOP_WORDS)
        self.assertIn("UNSUBSCRIBE", STOP_WORDS)

    def test_caller_number_must_be_e164(self):
        self.assertTrue(valid_caller_number("+61412345678"))
        self.assertFalse(valid_caller_number("anonymous"))
        self.assertFalse(valid_caller_number("0412345678"))

    def test_atomic_sms_rate_limit_and_release_on_send_failure(self):
        now = datetime.now(timezone.utc)
        since = (now - timedelta(hours=6)).isoformat()
        first = db.claim_sms_slot(self.clinic_id, "+61400000000", since, "callback", now.isoformat())
        second = db.claim_sms_slot(self.clinic_id, "+61400000000", since, "callback", now.isoformat())
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        db.release_sms_slot(first)
        self.assertIsNotNone(db.claim_sms_slot(self.clinic_id, "+61400000000", since, "retry", now.isoformat()))

    def test_stop_keyword_normalization(self):
        self.assertTrue(is_stop_keyword(" unsubscribe "))
        self.assertFalse(is_stop_keyword("please stop by reception"))

    def test_only_completed_or_answered_calls_are_not_missed(self):
        self.assertFalse(is_missed_call_status("completed"))
        self.assertFalse(is_missed_call_status("ANSWERED"))
        self.assertTrue(is_missed_call_status("no-answer"))
        self.assertTrue(is_missed_call_status("busy"))
        self.assertTrue(is_missed_call_status("failed"))
        self.assertTrue(is_missed_call_status("canceled"))


if __name__ == "__main__":
    unittest.main()
