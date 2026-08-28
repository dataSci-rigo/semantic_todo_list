"""Two-way TaskBus sync with the shared Google Tasks list (see TASKBUS.md).

Cycle order (run_sync):
  1. migrate     — re-home tasks linked to a pre-TaskBus list
  2. pull        — accept / merge / lock-restore remote changes
  3. push new    — parents before children, TB1 block v=1
  4. push dirty  — local edits, block v = last seen + 1

Human edits made in the Google Tasks UI carry no TB1 bump; they're detected
via the task's `updated` timestamp and treated as remote changes to the
native fields (title / notes / status).

Conflicts (both sides diverged from the same base) auto-merge: Haiku merges
the free text, done-status wins over open, our namespace fields stay ours,
foreign namespaces stay theirs — then a notification with ↩️ Revert goes to
Telegram. Locked tasks (locked_by_us) overwritten by another app are
restored and the overwrite reported.
"""
from __future__ import annotations

import json

import ai
import config
import db
import flows
import google_tasks as gt
import taskbus
import telegram_api as tg

APP = config.TASKBUS_APP_ID

_list_id: str | None = None
_recent_merges: dict[int, dict] = {}  # task_id -> pre-merge local copy (for revert)


def _get_list_id() -> str:
    global _list_id
    if _list_id is None:
        _list_id = gt.ensure_list(config.GOOGLE_TASKS_LIST_NAME)
    return _list_id


def _own_fields(task) -> dict:
    f: dict = {}
    if task["category"]:
        f["cat"] = task["category"]
    if task["urgency"] is not None:
        f["uiev"] = [task["urgency"], task["interest"], task["energy"], task["value"]]
    if task["priority"] is not None:
        f["pri"] = task["priority"]
    if task["estimated_active_minutes"] is not None:
        f["est"] = [task["estimated_active_minutes"], task["estimated_passive_minutes"] or 0]
    return f


def _foreign_of(block: dict | None) -> dict:
    f = dict((block or {}).get("f") or {})
    f.pop(APP, None)
    return f


def _local_dirty(task, link) -> bool:
    return bool(task["updated_at"] and link["last_synced_at"]
                and task["updated_at"] > link["last_synced_at"])


def _compose_notes(task, link, version: int, lock: bool) -> str:
    foreign = json.loads(link["foreign_json"]) if link and link["foreign_json"] else {}
    block = taskbus.make_block(APP, version, version - 1, _own_fields(task),
                                foreign=foreign, lock=lock)
    return taskbus.build_notes(task["description"] or "", block)


# ── 1. migrate links from a pre-TaskBus list ─────────────────────────────

def _migrate_old_links(list_id: str) -> None:
    for link in db.get_all_links():
        if link["google_list_id"] == list_id:
            continue
        task = db.get_task(link["task_id"])
        if task is None:
            continue
        notes = _compose_notes(task, link, 1, bool(link["locked_by_us"]))
        gtask = gt.insert_task(list_id, task["title"], notes, task["status"] == "done")
        try:
            gt.delete_task(link["google_list_id"], link["google_task_id"])
        except Exception:
            pass  # old list may already be gone
        db.update_link_bus_state(task["id"], 1, google_list_id=list_id,
                                  google_task_id=gtask["id"])
        db.touch_google_link(task["id"], gtask.get("updated"))


# ── 2. pull ──────────────────────────────────────────────────────────────

def _apply_remote(task_id: int, gtask: dict, human: str, block: dict | None) -> None:
    task = db.get_task(task_id)
    db.update_task(
        task_id,
        title=gtask.get("title") or task["title"],
        description=human or None,
        status="done" if gtask.get("status") == "completed"
               else ("draft" if task["status"] == "done" else task["status"]),
    )
    remote_v = int((block or {}).get("v", 0))
    db.update_link_bus_state(task_id, remote_v,
                              foreign_json=json.dumps(_foreign_of(block)))
    db.touch_google_link(task_id, gtask.get("updated"))


def _merge_conflict(list_id: str, task_id: int, gtask: dict, human: str,
                     block: dict | None, link) -> None:
    task = db.get_task(task_id)
    _recent_merges[task_id] = {"title": task["title"], "description": task["description"],
                                "status": task["status"]}
    ours = {"title": task["title"], "notes": task["description"] or "",
            "done": task["status"] == "done"}
    theirs = {"title": gtask.get("title") or "", "notes": human,
              "done": gtask.get("status") == "completed"}
    if ours["title"] == theirs["title"] and ours["notes"] == theirs["notes"]:
        merged = {"title": ours["title"], "notes": ours["notes"] or None}
    else:
        try:
            merged = ai.merge_task_versions(ours, theirs)
        except Exception as e:
            print(f"  merge AI error: {e}")
            merged = {"title": ours["title"], "notes": ours["notes"] or None}
    done = ours["done"] or theirs["done"]  # done wins

    db.update_task(task_id, title=merged["title"], description=merged["notes"],
                    status="done" if done else "draft")
    remote_v = int((block or {}).get("v", 0))
    foreign = _foreign_of(block)
    db.update_link_bus_state(task_id, remote_v, foreign_json=json.dumps(foreign))

    task = db.get_task(task_id)
    notes = _compose_notes(task, db.get_link_by_task(task_id), remote_v + 1,
                            bool(link["locked_by_us"]))
    result = gt.update_task(list_id, link["google_task_id"], title=task["title"],
                             notes=notes, completed=done)
    db.update_link_bus_state(task_id, remote_v + 1)
    db.touch_google_link(task_id, result.get("updated"))

    other = (block or {}).get("app", "another app")
    kb = {"inline_keyboard": [[{"text": "↩️ Revert my side", "callback_data": f"undo:merge:{task_id}"}]]}
    tg.send_message(f"🔀 Merged #{task_id} — both you and {other} edited it.\n"
                     f"Now: {task['title']}", reply_markup=kb)


def _restore_locked(list_id: str, task_id: int, block: dict | None, link) -> None:
    task = db.get_task(task_id)
    remote_v = int((block or {}).get("v", 0))
    notes = _compose_notes(task, link, remote_v + 1, lock=True)
    result = gt.update_task(list_id, link["google_task_id"], title=task["title"],
                             notes=notes, completed=task["status"] == "done")
    db.update_link_bus_state(task_id, remote_v + 1)
    db.touch_google_link(task_id, result.get("updated"))
    other = (block or {}).get("app", "another app")
    tg.send_message(f"🔒 #{task_id} is locked but {other} modified it — restored our copy.")


def _pull_changes(list_id: str) -> None:
    for gtask in gt.list_tasks(list_id):
        human, block = taskbus.parse_notes(gtask.get("notes"))
        link = db.get_link_by_google_id(gtask["id"])

        if link is None:
            task_id = db.create_task(gtask.get("title") or "(untitled)", human or None,
                                      "taskbus", None)
            if gtask.get("status") == "completed":
                db.update_task(task_id, status="done")
            db.link_google_task(task_id, gtask["id"], list_id, gtask.get("updated"))
            db.update_link_bus_state(task_id, int((block or {}).get("v", 0)),
                                      foreign_json=json.dumps(_foreign_of(block)))
            if gtask.get("status") != "completed":
                flows.start_classification(task_id, None)
            continue

        task = db.get_task(link["task_id"])
        if task is None:
            continue
        remote_v = int((block or {}).get("v", 0))
        remote_moved = (remote_v > link["version"]
                        or (gtask.get("updated") and link["google_updated"]
                            and gtask["updated"] > link["google_updated"]))
        if not remote_moved:
            continue
        if link["locked_by_us"] and block and block.get("app") != APP:
            _restore_locked(list_id, task["id"], block, link)
            continue
        if _local_dirty(task, link):
            _merge_conflict(list_id, task["id"], gtask, human, block, link)
        else:
            was_open = task["status"] != "done"
            _apply_remote(task["id"], gtask, human, block)
            if was_open and gtask.get("status") == "completed":
                print(f"  taskbus: #{task['id']} completed remotely")


# ── 3 + 4. push ──────────────────────────────────────────────────────────

def _push_new(list_id: str) -> None:
    unlinked = db.get_unlinked_tasks()
    for task in sorted(unlinked, key=lambda t: (t["parent_task_id"] is not None, t["id"])):
        parent_gid = None
        if task["parent_task_id"]:
            plink = db.get_link_by_task(task["parent_task_id"])
            parent_gid = plink["google_task_id"] if plink else None
        block = taskbus.next_write(None, APP, _own_fields(task))
        notes = taskbus.build_notes(task["description"] or "", block)
        gtask = gt.insert_task(list_id, task["title"], notes,
                                task["status"] == "done", parent=parent_gid)
        db.link_google_task(task["id"], gtask["id"], list_id, gtask.get("updated"))
        db.update_link_bus_state(task["id"], block["v"], foreign_json=json.dumps({}))


def _push_updates(list_id: str) -> None:
    for link in db.get_all_links():
        task = db.get_task(link["task_id"])
        if task is None or link["google_list_id"] != list_id:
            continue
        if not _local_dirty(task, link):
            continue
        new_v = link["version"] + 1
        notes = _compose_notes(task, link, new_v, bool(link["locked_by_us"]))
        result = gt.update_task(list_id, link["google_task_id"], title=task["title"],
                                 notes=notes, completed=task["status"] == "done")
        db.update_link_bus_state(task["id"], new_v)
        db.touch_google_link(task["id"], result.get("updated"))


# ── public API ───────────────────────────────────────────────────────────

def run_sync() -> None:
    try:
        list_id = _get_list_id()
        _migrate_old_links(list_id)
        _pull_changes(list_id)
        _push_new(list_id)
        _push_updates(list_id)
    except Exception as e:
        print(f"  google sync error: {e}")


def revert_merge(task_id: int) -> bool:
    """Restore the local pre-merge copy after an auto-merge (session-scoped)."""
    prior = _recent_merges.pop(task_id, None)
    if prior is None:
        return False
    db.update_task(task_id, **prior)
    return True


def set_lock(task_id: int, locked: bool) -> bool:
    task = db.get_task(task_id)
    if task is None or db.get_link_by_task(task_id) is None:
        return False
    db.set_link_locked(task_id, locked)
    # touch updated_at so the next cycle pushes the new lock state
    db.update_task(task_id, status=task["status"])
    return True
