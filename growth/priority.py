"""Today's P1–P5 from the TaskBus mirror (STM's tasks ⋈ google_task_links).

STM writes f.stm.pri from tasks.priority and never imports it back, so the
local column is the bus ranking; a Compass rank (f.compass.pri) arrives in
google_task_links.foreign_json. Never imports anthropic or telegram_api."""
from __future__ import annotations

import json
import sqlite3

from . import gdb, hooks

RANK_SQL = """
SELECT t.id, t.title, t.category, t.priority AS stm_pri,
       json_extract(g.foreign_json, '$.compass.pri') AS compass_pri,
       COALESCE(t.urgency,0)+COALESCE(t.value,0)+COALESCE(t.interest,0)-COALESCE(t.energy,0) AS score
FROM tasks t LEFT JOIN google_task_links g ON g.task_id = t.id
WHERE t.status NOT IN ('done','dropped')
  AND NOT EXISTS (SELECT 1 FROM tasks c WHERE c.parent_task_id = t.id)
"""


def _rank(row) -> tuple[int | None, str]:
    cp = row["compass_pri"]
    try:
        if cp is not None and 1 <= int(cp) <= 99:
            return int(cp), "compass"
    except (TypeError, ValueError):
        pass
    if row["stm_pri"] is not None:
        return int(row["stm_pri"]), "stm"
    return None, "score"


def ranked(limit: int = 5) -> list[dict]:
    """[{p, task_id, title, src}] — ranked tasks first, then score order."""
    try:
        rows = gdb._all(RANK_SQL)
    except sqlite3.OperationalError as e:
        print(f"  growth priority sql error: {e}")
        return _fallback(limit)
    scored = []
    for r in rows:
        rank, src = _rank(r)
        scored.append((0 if rank is not None else 1, rank if rank is not None else 0,
                       0 if src == "compass" else 1, -float(r["score"] or 0), r["id"], r["title"], src))
    scored.sort()
    return [{"p": i + 1, "task_id": s[4], "title": s[5], "src": s[6]} for i, s in enumerate(scored[:limit])]


def _fallback(limit: int) -> list[dict]:
    try:
        import db as stm_db
        rows = stm_db.get_tasks_by_priority(limit)
    except Exception as e:
        print(f"  growth priority fallback error: {e}")
        return []
    return [{"p": i + 1, "task_id": r["id"], "title": r["title"],
             "src": "stm" if r["priority"] is not None else "score"} for i, r in enumerate(rows)]


def source_of(plist: list[dict]) -> str:
    if not plist:
        return "none"
    srcs = {p["src"] for p in plist}
    if "manual" in srcs:
        return "manual"
    if srcs == {"score"}:
        return "score"
    return "compass" if "compass" in srcs else "stm"


def source_label(src: str | None) -> str:
    return {"compass": "Compass rank", "stm": "TaskBus rank", "score": "unranked, score order",
            "manual": "typed by hand", None: "no ranked tasks", "none": "no ranked tasks"}.get(src, src or "")


def refresh_and_snapshot(user_id: int, day: str, max_age: int = 120) -> list[dict]:
    """Sync the bus if stale (via the stm_bot hook), rank, store today's snapshot."""
    if hooks.sync_now is not None:
        try:
            hooks.sync_now(max_age)
        except Exception as e:
            print(f"  growth sync hook error: {e}")
    d = gdb.get_day(user_id, day)
    existing = gdb.loads(d["p_list"], []) if d and d["p_list"] else []
    if d and d["p_source"] == "manual" and existing:
        return existing                      # typed by hand today: the user's ranking wins
    plist = ranked(5)
    if not plist and existing:
        return existing                      # nothing ranked now: keep what we had
    gdb.update_day(user_id, day, p_list=json.dumps(plist, ensure_ascii=False), p_source=source_of(plist))
    return plist


def day_list(user_id: int, day: str) -> list[dict]:
    d = gdb.get_day(user_id, day)
    if d and d["p_list"]:
        plist = gdb.loads(d["p_list"], [])
        if plist:
            return plist
    plist = ranked(5)
    if plist:
        gdb.update_day(user_id, day, p_list=json.dumps(plist, ensure_ascii=False), p_source=source_of(plist))
    return plist


def swap(user_id: int, day: str, a: int, b: int) -> None:
    plist = day_list(user_id, day)
    by_p = {p["p"]: p for p in plist}
    if a in by_p and b in by_p:
        by_p[a], by_p[b] = by_p[b], by_p[a]
        for p_no, item in by_p.items():
            item["p"] = p_no
        plist = [by_p[k] for k in sorted(by_p)]
        gdb.update_day(user_id, day, p_list=json.dumps(plist, ensure_ascii=False))


def set_manual(user_id: int, day: str, titles: list[str]) -> None:
    plist = [{"p": i + 1, "task_id": None, "title": t[:80], "src": "manual"} for i, t in enumerate(titles)]
    gdb.update_day(user_id, day, p_list=json.dumps(plist, ensure_ascii=False), p_source="manual")
