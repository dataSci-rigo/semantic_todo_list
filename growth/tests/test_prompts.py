from growth import gdb, session
from growth.tests.conftest import USER_ID


def test_reply_to_and_last_message_rule(env):
    session.start(env.user, "win")
    mid = env.tg.last_id()
    # an unrelated bot message intervenes → plain text is NOT claimed
    env.tg.send_message("STM: saved a task", None, chat_id=USER_ID)
    assert not env.text("this should not be claimed")
    # replying to the card still works
    env.reply(mid, "called the bank")
    assert gdb.win_count(USER_ID) == 1
    assert "Kept" in env.tg.messages[mid]["text"]


def test_skip_and_expiry(env):
    session.start(env.user, "win_old")
    assert session.skip(USER_ID) == ""
    assert gdb.win_count(USER_ID) == 0
    session.start(env.user, "win")
    env.clock.advance(4 * 3600)
    assert not env.text("too late")
    assert session.skip(USER_ID) == "Nothing to skip."


def test_stale_callback_and_close(env):
    session.start(env.user, "refocus")
    mid = env.tg.last_id()
    env.tap("Close")
    assert gdb.get_touch(gdb.touch_by_message(USER_ID, mid)["id"])["status"] == "skipped"
    cq = {"id": "x", "message": {"chat": {"id": USER_ID}, "message_id": mid}, "from": {"id": USER_ID},
          "data": f"gb:{gdb.touch_by_message(USER_ID, mid)['id']}:number:p1"}
    session.handle_callback(cq, cq["data"].split(":"))
    assert env.tg.answers[-1] == "That card is closed."


def test_cancel(env):
    session.start(env.user, "surf")
    assert "timer cancelled" in session.cancel(USER_ID)
    assert session.cancel(USER_ID) is None
