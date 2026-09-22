"""In-loop scheduling: plan the day, fire due touches and timers, expire
stale cards, nightly close. Called every poll-loop iteration."""
from __future__ import annotations

import math
import random
from datetime import timedelta
from zoneinfo import ZoneInfo

from . import clock, gdb, levels, session, settings, bank

_last_nightly_check = ""


def _tz(user) -> ZoneInfo:
    try:
        return ZoneInfo(user["tz"] or settings.TZ_NAME)
    except Exception:
        return settings.TZ


def draw_refocus_times(centres: list[str], jitter_min: int = settings.REFOCUS_JITTER_MIN,
                       min_spacing_min: int = settings.REFOCUS_MIN_SPACING_MIN,
                       rng: random.Random | None = None) -> list[str]:
    """Jitter each HH:MM centre by ±jitter, keep them ≥ min_spacing apart and
    inside 06:00–23:00. Pure; seedable."""
    rng = rng or random.Random()
    mins = []
    for c in centres:
        h, m = (int(x) for x in c.split(":"))
        mins.append(h * 60 + m + rng.randint(-jitter_min, jitter_min))
    mins.sort()
    out = []
    for v in mins:
        v = max(6 * 60, min(23 * 60, v))
        if out and v - out[-1] < min_spacing_min:
            v = min(23 * 60, out[-1] + min_spacing_min)
        out.append(v)
    return [f"{v // 60:02d}:{v % 60:02d}" for v in out]


def plan_day(user, today: str) -> None:
    uid = user["user_id"]
    if gdb.touch_exists(uid, today, "morning", 1):
        return
    tz = _tz(user)
    gdb.ensure_day(uid, today, 1 if user["survival"] else 0)
    now = clock.now()
    rows = [("morning", 1, user["morning_time"])]
    windows = gdb.loads(user["refocus_windows"], settings.DEFAULT_REFOCUS_WINDOWS) or settings.DEFAULT_REFOCUS_WINDOWS
    times = draw_refocus_times([str(w) for w in windows])
    slots = [3, 5, 7]
    if user["survival"]:
        mid = times[len(times) // 2] if times else "13:30"
        rows.append(("refocus", 5, mid))
    else:
        for slot, t in zip(slots, times):
            rows.append(("refocus", slot, t))
    rows.append(("evening", 9, user["evening_time"]))
    for kind, slot, hhmm in rows:
        due = clock.local_dt(today, hhmm, tz)
        grace = settings.GRACE.get(kind, timedelta(hours=1))
        status = "scheduled" if now <= due + grace else "expired"
        try:
            gdb.create_touch(user_id=uid, day=today, kind=kind, slot=slot, chat_id=user["chat_id"],
                             thread_id=user["thread_id"], due_at=clock.iso(due), status=status)
        except Exception as e:  # UNIQUE race: already planned
            print(f"  growth plan_day skip {kind}/{slot}: {e}")
    gdb.update_day(uid, today, planned_at=clock.iso(now))


def _fire_due_touches(now_iso: str) -> None:
    now = clock.now()
    for t in gdb.due_touches(now_iso, settings.MAX_FIRES_PER_TICK):
        grace = settings.GRACE.get(t["kind"], timedelta(hours=1))
        if now - clock.parse(t["due_at"]) > grace:
            gdb.update_touch(t["id"], status="expired")
            continue
        try:
            session.fire(t)
        except Exception as e:
            print(f"  growth fire {t['kind']}#{t['id']} error: {e}")
            gdb.update_touch(t["id"], status="send_failed", send_attempts=int(t["send_attempts"]) + 1)
    for t in gdb.failed_touches(1):
        if t["fired_at"]:
            try:
                session.retry_send(t)
            except Exception as e:
                print(f"  growth retry error: {e}")
                gdb.update_touch(t["id"], send_attempts=int(t["send_attempts"]) + 1)
        else:
            try:
                session.fire(t)
            except Exception as e:
                print(f"  growth refire error: {e}")
                gdb.update_touch(t["id"], send_attempts=int(t["send_attempts"]) + 1)


def _fire_due_timers(now_iso: str) -> None:
    now = clock.now()
    for timer in gdb.due_timers(now_iso):
        if now - clock.parse(timer["due_at"]) > settings.TIMER_LATE_EXPIRE:
            gdb.update_timer(timer["id"], status="expired")
            touch = gdb.get_touch(timer["touch_id"]) if timer["touch_id"] else None
            if touch is not None and touch["status"] == "active":
                session.close(touch, "Timer lost in a restart.", "expired")
            continue
        try:
            session.on_timer_fired(timer)
        except Exception as e:
            print(f"  growth timer #{timer['id']} error: {e}")


def _expire_stale(now_iso: str) -> None:
    for t in gdb.stale_active_touches(now_iso):
        try:
            session.expire(t)
        except Exception as e:
            print(f"  growth expire error: {e}")
    gdb.expire_prompts(now_iso)


def _nightly(user, today: str) -> None:
    tz = _tz(user)
    if clock.local_now(tz).hour < settings.NIGHTLY_HOUR:
        return
    yesterday = clock.day_offset(today, -1)
    d = gdb.get_day(user["user_id"], yesterday)
    if d is None or d["closed_at"]:
        return
    gdb.update_day(user["user_id"], yesterday, closed_at=clock.iso(clock.now()))
    try:
        levels.nightly(user, today)
        cur, _ = levels.streak(user["user_id"], yesterday)
        if cur == settings.STREAK_GOAL:
            from . import loader
            bank.add_system(user["user_id"], loader.content().copy["streak_goal"], yesterday)
    except Exception as e:
        print(f"  growth nightly error: {e}")
    try:
        gdb.wal_checkpoint()
    except Exception:
        pass


def tick() -> None:
    now_iso = clock.iso(clock.now())
    for user in gdb.active_users():
        today = clock.local_today(_tz(user))
        try:
            plan_day(user, today)
        except Exception as e:
            print(f"  growth plan_day error: {e}")
        try:
            _nightly(user, today)
        except Exception as e:
            print(f"  growth nightly error: {e}")
    _fire_due_touches(now_iso)
    _fire_due_timers(now_iso)
    _expire_stale(now_iso)


def poll_timeout(default: int = 30) -> int:
    nxt = gdb.next_due_iso()
    if not nxt:
        return default
    secs = (clock.parse(nxt) - clock.now()).total_seconds()
    return max(1, min(default, int(math.ceil(secs))))


def busy() -> bool:
    now = clock.now()
    nxt = gdb.next_due_iso()
    if nxt and (clock.parse(nxt) - now).total_seconds() < settings.BUSY_HORIZON_SECONDS:
        return True
    for user in gdb.active_users():
        p = gdb.latest_prompt(user["user_id"])
        if p and (now - clock.parse(p["created_at"])).total_seconds() < 600:
            return True
        f = gdb.latest_fired_at(user["user_id"])
        if f and (now - clock.parse(f)).total_seconds() < 120:
            return True
    return False
