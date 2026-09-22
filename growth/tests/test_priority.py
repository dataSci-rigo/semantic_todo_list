import json

import db as stm_db
from growth import gdb, priority
from growth.tests.conftest import USER_ID


def _task(title, prio=None, urgency=0, value=0, parent=None):
    tid = stm_db.create_task(title, None, "test", None)
    stm_db.update_task(tid, priority=prio, urgency=urgency, value=value, parent_task_id=parent)
    return tid


def test_rank_compass_over_stm_then_score(env):
    a = _task("stm 2", prio=2)
    b = _task("stm 1", prio=1)
    c = _task("compass 1", prio=9)
    d = _task("unranked hot", urgency=5, value=5)
    e = _task("unranked cold")
    stm_db.link_google_task(c, "g1", "list", None)
    stm_db.update_link_bus_state(c, 3, foreign_json=json.dumps({"compass": {"pri": 1}}))
    plist = priority.ranked(5)
    assert [p["title"] for p in plist] == ["compass 1", "stm 1", "stm 2", "unranked hot", "unranked cold"]
    assert plist[0]["src"] == "compass" and plist[1]["src"] == "stm" and plist[3]["src"] == "score"


def test_leaf_only_and_malformed_json(env):
    parent = _task("project", prio=1)
    _task("child", prio=2, parent=parent)
    bad = _task("bad json", prio=3)
    stm_db.link_google_task(bad, "g2", "list", None)
    stm_db.update_link_bus_state(bad, 1, foreign_json="{not json")
    titles = [p["title"] for p in priority.ranked(5)]
    assert "project" not in titles and "child" in titles and "bad json" in titles


def test_day_snapshot_and_swap(env):
    _task("one", prio=1)
    _task("two", prio=2)
    plist = priority.day_list(USER_ID, "2026-09-18")
    assert [p["p"] for p in plist] == [1, 2]
    priority.swap(USER_ID, "2026-09-18", 1, 2)
    plist = priority.day_list(USER_ID, "2026-09-18")
    assert [p["title"] for p in plist] == ["two", "one"]
    priority.set_manual(USER_ID, "2026-09-18", ["a", "b", "c"])
    assert gdb.get_day(USER_ID, "2026-09-18")["p_source"] == "manual"
