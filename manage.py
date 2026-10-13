import argparse

from app.db import connect, init_db


def main():
    parser = argparse.ArgumentParser(description="Manage MCTB clinics")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add-clinic", help="Add a clinic")
    add.add_argument("--name", required=True)
    add.add_argument("--twilio-number", required=True)
    add.add_argument("--forward-to-number", required=True)
    add.add_argument("--booking-link", default="")
    add.add_argument("--sms-template", default="")
    add.add_argument("--notify-email", default="")
    add.add_argument("--notify-sms", default="")
    add.add_argument("--timezone", default="Australia/Sydney")
    add.add_argument("--business-hours-mode", choices=("always", "after-hours-only", "custom-schedule"), default="always")
    add.add_argument("--business-hours-json", default="{}", help='Example: {"mon":[["09:00","17:00"]]}')
    args = parser.parse_args()
    init_db()
    with connect() as db:
        cursor = db.execute("""INSERT INTO clinics(name,twilio_number,forward_to_number,booking_link,
            sms_template,notify_email,notify_sms,timezone,business_hours_mode,business_hours_json)
            VALUES(?,?,?,?,?,?,?,?,?,?)""", (args.name, args.twilio_number, args.forward_to_number,
            args.booking_link, args.sms_template, args.notify_email, args.notify_sms, args.timezone,
            args.business_hours_mode, args.business_hours_json))
    print(f"Added clinic id={cursor.lastrowid}")


if __name__ == "__main__":
    main()
