# Missed-Call Text-Back (MCTB)

A small FastAPI service for clinics: it answers a Twilio voice webhook, rings the clinic desk for 20 seconds, then sends one generic callback SMS if the call was missed. Incoming replies are logged and forwarded to the configured staff SMS destination.

## Project layout

`app/main.py` contains the webhooks and admin report; `app/db.py` owns SQLite; `app/schedule.py` checks local business hours; `app/notifications.py` sends staff notifications; `manage.py` provisions clinics; `tests/` contains unit tests.

## Run locally

Requires Python 3.11. Create a virtual environment, install dependencies, copy `.env.example` to `.env`, and set real credentials and a public HTTPS base URL. Export those values into the shell (or use your preferred environment loader); the service intentionally does not load `.env` itself.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Add a clinic (numbers must be E.164, e.g. `+612...`):

```sh
python manage.py add-clinic --name "Harbour Clinic" --twilio-number +61280000000 \
  --forward-to-number +61280000001 --booking-link https://clinic.example/book \
  --notify-email reception@example.com --notify-sms +61400000000 --timezone Australia/Sydney
```

The default SMS is generic and has only `{clinic_name}` and `{booking_link}` placeholders. Messages include a `Reply STOP to opt out` instruction; that instruction is added automatically if a custom template omits STOP/UNSUBSCRIBE. Clinic message text must not request or include health details. Keep templates limited to sender identification and a callback/booking prompt.

Business hours are optional. `always` sends any time. `after-hours-only` considers weekdays 09:00–17:00 local time open. `custom-schedule` treats the supplied weekly intervals as open hours and sends only outside them. Example:

```sh
python manage.py add-clinic --name "Harbour Clinic" --twilio-number +61280000000 \
  --forward-to-number +61280000001 --business-hours-mode custom-schedule \
  --business-hours-json '{"mon":[["08:30","17:30"]],"tue":[["08:30","17:30"]]}' \
  --timezone Australia/Sydney
```

Times are local `HH:MM` 24-hour values; days use `mon` through `sun`. Configure `PUBLIC_BASE_URL` to the exact externally visible HTTPS origin, without a trailing slash. This is used to validate Twilio signatures behind a proxy.

## Twilio setup

1. Create a Twilio account and configure its Account SID and Auth Token as `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`. Set a long random `ADMIN_API_KEY` too.
2. Buy an Australian number in Twilio Console. AU number availability, address requirements, allowed use, and regulatory bundles can vary by number type and change over time. Complete the current Australian regulatory bundle / customer identity and address requirements shown in Twilio Console before use. A number's ability to send SMS depends on its type and current Twilio AU rules; verify voice and SMS capability before buying.
3. In the phone number's Voice configuration, set **A call comes in** to `https://YOUR_HOST/voice/incoming` using HTTP POST. Set the number's Messaging configuration **A message comes in** to `https://YOUR_HOST/sms/incoming` using HTTP POST.
4. `voice/incoming` returns a `<Dial timeout="20">` with `/voice/status` as its action callback. The callback is also configured for POST.
5. Provide `--notify-sms` for staff SMS notifications. They use the clinic's Twilio number. `notify_email` remains available in the clinic record for configuration compatibility but is not used for notifications.

For local testing, run `ngrok http 8000`, set `PUBLIC_BASE_URL` to the HTTPS forwarding origin ngrok gives you, and use those webhook URLs in Twilio. Restart the app whenever the ngrok URL changes. Twilio signatures are validated on every webhook; unsigned local curl requests are expected to fail.

## Deploy

The included Dockerfile works on Railway and Render. Deploy from this repository, set the environment variables in the host dashboard, and use the generated HTTPS service URL as `PUBLIC_BASE_URL`. Configure a persistent volume and set `DATABASE_PATH` to a file on it: the container filesystem may be ephemeral. Run `python manage.py add-clinic ...` against the deployed environment once the volume is mounted (or run it as a one-off service command). The app initializes the schema at startup.

## Admin report

```sh
curl -H 'X-Admin-Key: YOUR_ADMIN_API_KEY' \
  'https://YOUR_HOST/admin/report?clinic_id=1&days=30'
```

Returns missed call count, sent-text count, reply count, and `reply_rate` (replies divided by texts sent). `days` is 1–365. Reports use UTC event timestamps.

## Privacy, opt-outs, and Australia

- The service stores only the caller phone number, event timestamp, and SMS message text, plus the clinic configuration needed to route calls and messages. It does not collect call audio or health information. Keep templates and replies generic; never ask for or send health information by SMS.
- A caller who sends STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, or QUIT is stored in the clinic-specific opt-out table and is not sent future automated texts. Staff are not forwarded an opt-out keyword message. The app does not send a separate confirmation text.
- Australian commercial messages must comply with the [Spam Act 2003](https://www.legislation.gov.au/C2004A01214/latest/text), including accurate sender identification/contact details and a functional unsubscribe facility. ACMA says unsubscribe requests should be completed within five working days; this service records supported opt-out keywords immediately and blocks subsequent auto-texts. Include the clinic name, monitor replies, and keep unsubscribe handling enabled. Confirm that the clinic has the required consent or other lawful basis for its messaging use. This software is not legal advice. See [ACMA's guidance for senders](https://www.acma.gov.au/avoid-sending-spam).
- Twilio's Australia rules vary by number and sender type. Australian SMS traffic may require business identity details, and Twilio-number regulatory bundles/address requirements can vary by number type. Alphanumeric Sender IDs have a separate ACMA registration regime. Check the current [Twilio AU SMS guidelines](https://www.twilio.com/en-us/guidelines/au/sms), [Twilio number regulatory requirements](https://www.twilio.com/docs/phone-numbers/regulatory), and Twilio Console before purchase and launch; requirements can change.
- Protect admin access and the database volume. HTTPS is required in production. Configure an appropriate retention and backup policy for message text.

## Clinic call forwarding

Twilio must receive the call before this service can decide whether to text back. Clinics can use carrier-level **conditional forwarding** for no-answer/busy to route calls to the Twilio number while keeping their regular published number. Carrier menus, activation codes, and feature support differ by plan and change; ask the clinic's AU carrier to enable conditional forwarding to the Twilio number for **no answer** and **busy**, and test both conditions. Some carriers require a star/hash activation code; obtain the exact current code from that carrier rather than reusing a code from another network. Set the forwarding delay to roughly 20 seconds if supported.

For an existing PBX/VoIP system, configure its no-answer and busy rules to forward the inbound call to the Twilio number after the desired ring timeout, and ensure caller ID is preserved. Verify that forwarding does not create a loop and that Twilio can return the caller's number. If the clinic wants the desk to ring first, configure the normal published number's conditional forward after its ring timeout; the Twilio `<Dial>` then rings the clinic desk for up to 20 seconds before the callback SMS is sent.

## Tests and end-to-end checklist

Run unit tests with `python -m unittest discover -s tests`. They cover rate-limit boundary behavior, E.164 caller filtering, and opt-out storage/keywords.

1. Add a test clinic with a Twilio voice/SMS number, a real desk number, and a booking link.
2. Point Twilio's voice and messaging webhooks at the HTTPS app URLs and confirm both requests succeed with valid signatures.
3. Call the Twilio number and answer the desk phone: confirm no callback text is sent.
4. Repeat and let the desk ring out; confirm one generic callback text arrives within seconds.
5. Call again within six hours; confirm there is no second automated text. Repeat after six hours to verify the window expires.
6. Reply with STOP, then create another missed call; confirm no automated text is sent. Check opt-out is limited to that clinic.
7. Reply with a normal message; confirm staff notification includes the caller's number and reply, and the SMS webhook returns empty `<Response/>` TwiML.
8. Try `/admin/report` without and with `X-Admin-Key`; verify the counts and reply rate.
9. Check after-hours settings in the clinic's configured timezone and test no-answer/busy carrier or PBX forwarding without creating a forwarding loop.
