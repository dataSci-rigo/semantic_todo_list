"""A scripted day through the real step engine with a fake Telegram."""
from growth import gdb, priority, sched, session
from growth.tests.conftest import USER_ID


def _seed(env):
    priority.set_manual(USER_ID, "2026-09-18", ["Gate hinge", "Tax letter", "Optometrist"])
    gdb.set_core(USER_ID, "rest_menu", {"restores": [{"activity": "coffee", "minutes": 20}],
                                        "feels_like": ["geopolitics videos"]})
    gdb.set_core(USER_ID, "why", "to be useful")
    gdb.set_core(USER_ID, "roles", ["father", "engineer"])


def test_morning_then_refocus_one_tap(env):
    _seed(env)
    session.start(env.user, "morning")
    assert "Capacity" in env.card()
    env.tap("2 h")
    env.tap("OK")
    assert "Gate hinge" in env.card()
    env.tap("Confirm")
    env.tap("P1")            # challenge 1
    env.tap("P2")            # challenge 2
    env.tap("geopolitics")   # I won't (always-on elective)
    env.tap("coffee")        # rest budget toggle
    env.tap("Done")
    assert "failure point" in env.card().lower()
    assert env.text("2 pm, after lunch, news")
    # observation intro / franklin / core_field / visualize may follow; close remaining electives
    while env.tg.last().get("kb") and env.tg.last()["kb"]["inline_keyboard"]:
        btns = [b for b, _ in env.tg.buttons()]
        if "Skip" in btns:
            env.tap("Skip")
        elif "Not today" in btns:
            env.tap("Not today")
        elif "Next" in btns:
            env.tap("Next")
        else:
            env.tap("Close")
    day = gdb.get_day(USER_ID, "2026-09-18")
    assert day["capacity"] == 2 and day["sleep"] == "ok" and day["wont"] == "geopolitics videos"
    assert len(gdb.challenges_for_day(USER_ID, "2026-09-18")) == 2
    assert gdb.loads(day["rest_budget"])[0]["activity"] == "coffee"
    assert day["failure_point"].startswith("2 pm")

    session.start(env.user, "refocus")
    env.tap("P1")
    assert "P1, on it." in env.card()
    assert gdb.refocus_logs(USER_ID, ["2026-09-18"])[0]["on_p"] == 1
    assert gdb.events(USER_ID, "refocus_answered")


def test_refocus_other_excuse_surf_start5(env):
    _seed(env)
    session.start(env.user, "refocus")
    env.tap("Other")
    assert "Excuse?" in env.card()
    env.tap("P5 is more interesting")
    assert "not the priority" in env.card()
    env.tap("Surf 90 s")
    mid = env.tg.last_id()              # the refocus card; tick() may send other scheduled cards
    assert "Ends" in env.tg.messages[mid]["text"]
    env.clock.advance(91)
    sched.tick()
    assert "Ends" in env.tg.messages[mid]["text"]          # start5 timer now running, same card
    env.clock.advance(301)
    sched.tick()
    assert "5 min on Gate hinge logged" in env.tg.messages[mid]["text"]
    assert gdb.events(USER_ID, "excuse_logged") and gdb.events(USER_ID, "start5_completed")
    assert gdb.events(USER_ID, "surf_completed")
    log = gdb.refocus_logs(USER_ID, ["2026-09-18"])[0]
    assert log["on_kind"] == "other" and log["interest_flag"] == 1


def test_aim_end_and_evening(env):
    _seed(env)
    session.start(env.user, "aim")
    env.tap("P1")
    assert "Done looks like" in env.card()
    assert env.text("hinge off, new one on")
    env.tap("Skip")            # why (skippable at C1)
    env.tap("45")
    assert "Started" in env.card() and "/end" in env.card()
    ch = gdb.open_challenge(USER_ID)
    assert ch["task_title"] == "Gate hinge" and ch["est_min"] == 45
    session.start(env.user, "end", challenge_id=ch["id"])
    env.tap("4")
    assert env.text("stuck on the top screw; next: drill it out")
    env.tap("60")
    assert "Estimated 45, actual 60" in env.card()
    env.tap("Next small step")
    assert env.text("buy a bit")
    assert "moved 4/5" in env.card()
    ev = gdb.events(USER_ID, "challenge_ended")
    meta = gdb.loads(ev[0]["meta_json"])
    assert meta["moved"] == 4 and meta["resume_note"] == "present" and meta["tier"] == "t30"
    assert gdb.open_challenge(USER_ID) is None

    session.start(env.user, "evening")
    assert "done or rolled" in env.card()
    env.tap("Done")
    assert "proud" in env.card().lower()
    assert env.text("did the hinge instead of the video")
    while env.tg.last().get("kb") and env.tg.last()["kb"]["inline_keyboard"]:
        btns = [b for b, _ in env.tg.buttons()]
        env.tap("Skip" if "Skip" in btns else ("Next" if "Next" in btns else "Close"))
    assert gdb.win_count(USER_ID) == 1
    assert gdb.events(USER_ID, "evening_logged")
    assert "days with at least one touch" in env.card()
