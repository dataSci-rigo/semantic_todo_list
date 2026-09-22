from datetime import datetime, timezone

from growth import gdb, sched, session
from growth.tests.conftest import USER_ID


def test_plan_day_and_fire_and_expire(env):
    sched.tick()
    rows = gdb.touches_for_day(USER_ID, "2026-09-18")
    kinds = sorted((r["kind"], r["slot"], r["status"]) for r in rows)
    assert ("morning", 1, "active") in kinds           # 07:30 is 30 min before the 08:00 start: within grace, fires
    assert len([k for k in kinds if k[0] == "refocus"]) == 3
    assert ("evening", 9, "scheduled") in kinds


def test_morning_fires_within_grace(env):
    env.clock.set_now(datetime(2026, 9, 18, 14, 40, tzinfo=timezone.utc))  # 07:40 LA
    sched.tick()
    m = [r for r in gdb.touches_for_day(USER_ID, "2026-09-18") if r["kind"] == "morning"][0]
    assert m["status"] == "active" and "Capacity" in env.card()
    assert sched.busy()


def test_refocus_expires_after_grace(env):
    sched.tick()
    r = [t for t in gdb.touches_for_day(USER_ID, "2026-09-18") if t["kind"] == "refocus"][0]
    due = env.clock.parse(r["due_at"])
    env.clock.set_now(due + env.clock.timedelta(minutes=31))
    sched.tick()
    assert gdb.get_touch(r["id"])["status"] == "expired"


def test_poll_timeout_tracks_next_due(env):
    session.start(env.user, "surf")
    assert 1 <= sched.poll_timeout(30) <= 30
    env.clock.advance(80)
    assert sched.poll_timeout(30) <= 11


def test_active_card_expires(env):
    session.start(env.user, "win")
    env.clock.advance(4 * 3600)
    sched.tick()
    assert "(expired)" in env.card()


def test_nightly_closes_day_and_streak(env):
    gdb.touch_activity(USER_ID, "2026-09-17", done=True)
    gdb.ensure_day(USER_ID, "2026-09-17")
    env.clock.set_now(datetime(2026, 9, 18, 11, 0, tzinfo=timezone.utc))  # 04:00 LA
    sched.tick()
    assert gdb.get_day(USER_ID, "2026-09-17")["closed_at"]
    from growth import levels
    assert levels.streak(USER_ID, "2026-09-18") == (1, 1)
