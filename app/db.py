import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def database_path() -> str:
    return os.getenv("DATABASE_PATH", "./data/mctb.sqlite3")


@contextmanager
def connect():
    path = database_path()
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS clinics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            twilio_number TEXT NOT NULL UNIQUE,
            forward_to_number TEXT NOT NULL,
            booking_link TEXT NOT NULL DEFAULT '',
            sms_template TEXT NOT NULL,
            notify_email TEXT NOT NULL DEFAULT '',
            notify_sms TEXT NOT NULL DEFAULT '',
            timezone TEXT NOT NULL DEFAULT 'Australia/Sydney',
            business_hours_mode TEXT NOT NULL DEFAULT 'always'
                CHECK (business_hours_mode IN ('always','after-hours-only','custom-schedule')),
            business_hours_json TEXT NOT NULL DEFAULT '{}',
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clinic_id INTEGER NOT NULL REFERENCES clinics(id),
            caller_number TEXT NOT NULL,
            type TEXT NOT NULL CHECK (type IN ('missed_call','sms_sent','reply','opt_out')),
            timestamp TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_events_clinic_type_time
            ON events(clinic_id, type, timestamp);
        CREATE TABLE IF NOT EXISTS optouts (
            clinic_id INTEGER NOT NULL REFERENCES clinics(id),
            number TEXT NOT NULL,
            PRIMARY KEY (clinic_id, number)
        );
        """)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clinic_by_number(number: str):
    with connect() as db:
        return db.execute("SELECT * FROM clinics WHERE twilio_number=? AND active=1", (number,)).fetchone()


def add_event(clinic_id: int, caller: str, event_type: str, body: str = "", timestamp: str | None = None) -> None:
    with connect() as db:
        db.execute("INSERT INTO events(clinic_id,caller_number,type,timestamp,body) VALUES(?,?,?,?,?)",
                   (clinic_id, caller, event_type, timestamp or now_iso(), body))


def opted_out(clinic_id: int, number: str) -> bool:
    with connect() as db:
        return db.execute("SELECT 1 FROM optouts WHERE clinic_id=? AND number=?", (clinic_id, number)).fetchone() is not None


def add_optout(clinic_id: int, number: str) -> None:
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO optouts(clinic_id,number) VALUES(?,?)", (clinic_id, number))


def recently_texted(clinic_id: int, number: str, since_iso: str) -> bool:
    with connect() as db:
        return db.execute("""SELECT 1 FROM events WHERE clinic_id=? AND caller_number=?
            AND type='sms_sent' AND timestamp>=? LIMIT 1""", (clinic_id, number, since_iso)).fetchone() is not None


def claim_sms_slot(clinic_id: int, number: str, since_iso: str, body: str, timestamp: str | None = None) -> int | None:
    """Atomically reserve the six-hour slot; remove the event if Twilio send fails."""
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        recent = db.execute("""SELECT 1 FROM events WHERE clinic_id=? AND caller_number=?
            AND type='sms_sent' AND timestamp>=? LIMIT 1""", (clinic_id, number, since_iso)).fetchone()
        if recent:
            return None
        cur = db.execute("INSERT INTO events(clinic_id,caller_number,type,timestamp,body) VALUES(?,?,'sms_sent',?,?)",
                         (clinic_id, number, timestamp or now_iso(), body))
        return cur.lastrowid


def release_sms_slot(event_id: int) -> None:
    with connect() as db:
        db.execute("DELETE FROM events WHERE id=? AND type='sms_sent'", (event_id,))


def report(clinic_id: int, since_iso: str) -> dict:
    with connect() as db:
        row = db.execute("""SELECT
          SUM(CASE WHEN type='missed_call' THEN 1 ELSE 0 END) missed_calls,
          SUM(CASE WHEN type='sms_sent' THEN 1 ELSE 0 END) texts_sent,
          SUM(CASE WHEN type='reply' THEN 1 ELSE 0 END) replies
          FROM events WHERE clinic_id=? AND timestamp>=?""", (clinic_id, since_iso)).fetchone()
    missed, sent, replies = (int(row[k] or 0) for k in ("missed_calls", "texts_sent", "replies"))
    return {"clinic_id": clinic_id, "total_missed_calls": missed, "texts_sent": sent,
            "replies_received": replies, "reply_rate": round(replies / sent, 4) if sent else 0.0}


def parse_schedule(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}
