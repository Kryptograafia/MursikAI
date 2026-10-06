"""What "today" means for this assistant.

Lambda runs on UTC. Estonia is two or three hours ahead, so a task added at
00:30 local time would be filed under the previous day if UTC were used. Every
date in the system comes from here.

The IANA database is shipped with the function (the tzdata package). If it is
somehow missing, fall back to the EU daylight saving rules rather than silently
drifting to UTC dates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ZONE_NAME = "Europe/Tallinn"

WINTER = timedelta(hours=2)
SUMMER = timedelta(hours=3)


def tallinn_now(utc_instant: datetime | None = None) -> datetime:
    """Current Tallinn time, or the given UTC instant expressed in Tallinn time."""
    instant = _as_utc(utc_instant)
    zone = _load_zone()
    if zone is not None:
        return instant.astimezone(zone)
    return instant.astimezone(timezone(_eu_offset(instant)))


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("utc_instant must carry a timezone")
    return value.astimezone(timezone.utc)


def _load_zone() -> ZoneInfo | None:
    try:
        return ZoneInfo(ZONE_NAME)
    except ZoneInfoNotFoundError:
        return None


def _eu_offset(instant: datetime) -> timedelta:
    """Summer time runs from the last Sunday of March to the last Sunday of
    October, switching at 01:00 UTC on both days."""
    year = instant.year
    starts = _switch_instant(year, 3)
    ends = _switch_instant(year, 10)
    return SUMMER if starts <= instant < ends else WINTER


def _switch_instant(year: int, month: int) -> datetime:
    return datetime(year, month, _last_sunday(year, month), 1, 0, tzinfo=timezone.utc)


def _last_sunday(year: int, month: int) -> int:
    first_of_next = datetime(year + (month == 12), month % 12 + 1, 1)
    last_day = first_of_next - timedelta(days=1)
    # Monday is 0 in weekday(), so Sunday is 6; step back to the previous Sunday.
    return (last_day - timedelta(days=(last_day.weekday() + 1) % 7)).day
