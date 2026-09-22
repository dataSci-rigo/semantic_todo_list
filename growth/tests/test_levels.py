from growth import gdb, levels
from growth.tests.conftest import USER_ID


def _ev(name, day, **meta):
    gdb.emit(USER_ID, day, name, meta)


def test_count_days_ratio_and_levelup(env):
    today = "2026-09-18"
    for i in range(12):
        day = env.clock.day_offset(today, -i)
        gdb.ensure_day(USER_ID, day)
        _ev("evening_logged", day)
    for f in ["why", "eulogy", "fixed_areas", "top_interest", "signature_strengths", "avoided_tasks",
              "rest_restores", "rest_feels_like", "franklin_virtues", "roles"]:
        _ev("core_field_set", today, field=f)
    prog = levels.progress(USER_ID, today)
    assert prog["A"]["level"] == 1
    assert all(r["passed"] for r in prog["A"]["results"])
    lines = levels.nightly(env.user, today)
    assert any("Frame A2" in ln for ln in lines)
    assert gdb.levels(USER_ID)["A"] == 2
    assert gdb.wins(USER_ID, include_system=True)[0]["source"] == "system"
    # never regress: evaluating again with no new events keeps A2
    levels.nightly(env.user, today)
    assert gdb.levels(USER_ID)["A"] == 2


def test_where_filters_and_ratio(env):
    today = "2026-09-18"
    gdb.ensure_day(USER_ID, today)
    for moved in (5, 4, 4, 2, 1, 4):
        _ev("challenge_ended", today, moved=moved, tier="t30", resume_note="present")
    m = levels.evaluate_metric(USER_ID, today, {"id": "x", "kind": "count", "event": "challenge_ended",
                                                "where": {"moved_gte": 4}, "min": 6, "window_days": 28})
    assert m["current"] == 4 and not m["passed"]
    for v in ("yes", "yes", "no", "yes", "yes"):
        _ev("rest_checked", today, rest_restored=v)
    r = levels.evaluate_metric(USER_ID, today, {"id": "r", "kind": "ratio", "event": "rest_checked",
                                                "numerator_where": {"rest_restored": "yes"}, "min_ratio": 0.7,
                                                "min_n": 5, "window_days": 14})
    assert r["passed"] and r["current"] == 0.8


def test_survival_freezes(env):
    gdb.update_user(USER_ID, survival=1)
    assert levels.nightly(env.user, "2026-09-18") == []


def test_window_ignores_survival_days(env):
    today = "2026-09-18"
    gdb.ensure_day(USER_ID, today)
    gdb.ensure_day(USER_ID, env.clock.day_offset(today, -1), survival=1)
    _ev("evening_logged", env.clock.day_offset(today, -1))
    m = levels.evaluate_metric(USER_ID, today, {"kind": "days", "event": "evening_logged", "min": 1, "window_days": 1})
    assert m["current"] == 0
