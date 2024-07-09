import datetime

from zoneinfo import ZoneInfo


def now_aest() -> datetime.datetime:
    "Utility function to return now as TZ-aware datetime"
    return datetime.datetime.now(ZoneInfo('Australia/Melbourne'))
