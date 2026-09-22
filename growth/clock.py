"""Time seam: everything in growth asks this module for the time so that
simulate.py and the tests can move the clock without sleeping."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(timezone.utc) + _offset


def set_offset(delta: timedelta) -> None:
    global _offset
    _offset = delta


def advance(seconds: float) -> None:
    global _offset
    _offset += timedelta(seconds=seconds)


def set_now(dt: datetime) -> None:
    """Pin the clock to an absolute aware datetime (tests/simulate)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    set_offset(dt - datetime.now(timezone.utc))


def reset() -> None:
    set_offset(timedelta(0))


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def local_now(tz: ZoneInfo) -> datetime:
    return now().astimezone(tz)


def local_today(tz: ZoneInfo) -> str:
    return local_now(tz).strftime("%Y-%m-%d")


def local_dt(day: str, hhmm: str, tz: ZoneInfo) -> datetime:
    """Aware UTC datetime for a local date + 'HH:MM'."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = datetime.combine(date.fromisoformat(day), time(h, m), tzinfo=tz)
    return local.astimezone(timezone.utc)


def hhmm_local(dt_iso: str, tz: ZoneInfo) -> str:
    return parse(dt_iso).astimezone(tz).strftime("%H:%M")


def day_offset(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def weekday(day: str) -> int:
    """Monday = 0 … Sunday = 6."""
    return date.fromisoformat(day).weekday()
