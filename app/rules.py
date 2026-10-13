import re

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}


def valid_caller_number(number: str) -> bool:
    return bool(E164.fullmatch(number or ""))


def is_stop_keyword(body: str) -> bool:
    return (body or "").strip().upper() in STOP_WORDS


def is_missed_call_status(status: str) -> bool:
    return (status or "").strip().lower() not in {"completed", "answered"}
