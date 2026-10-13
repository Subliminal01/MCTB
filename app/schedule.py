from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .db import parse_schedule

DEFAULT_HOURS = {day: [["09:00", "17:00"]] for day in ("mon", "tue", "wed", "thu", "fri")}


def _is_open(schedule: dict, local: datetime) -> bool:
    intervals = schedule.get(local.strftime("%a").lower(), [])
    if not isinstance(intervals, list):
        return False
    for interval in intervals:
        try:
            start, end = (time.fromisoformat(value) for value in interval)
            current = local.timetz().replace(tzinfo=None)
            if start <= end and start <= current < end:
                return True
            if start > end and (current >= start or current < end):
                return True
        except (TypeError, ValueError):
            continue
    return False


def should_text(clinic, at: datetime) -> bool:
    mode = clinic["business_hours_mode"]
    if mode == "always":
        return True
    try:
        local = at.astimezone(ZoneInfo(clinic["timezone"]))
    except (ZoneInfoNotFoundError, KeyError):
        return False
    schedule = parse_schedule(clinic["business_hours_json"])
    if mode == "after-hours-only":
        schedule = DEFAULT_HOURS
    return not _is_open(schedule, local)
