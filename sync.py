"""Two-way sync between our tasks and a dedicated Google Tasks list.
Conflict resolution is last-write-wins by updated timestamp — pushed local
changes always land on Google before we pull, so we never immediately
overwrite what we just pushed."""
from __future__ import annotations

import config
import db
import flows
import google_tasks as gt

_list_id: str | None = None


def _get_list_id() -> str:
    global _list_id
    if _list_id is None:
        _list_id = gt.ensure_list(config.GOOGLE_TASKS_LIST_NAME)
    return _list_id


def _push_new(list_id: str) -> None:
    for task in db.get_unlinked_tasks():
        gtask = gt.insert_task(list_id, task["title"], task["description"], task["status"] == "done")
        db.link_google_task(task["id"], gtask["id"], list_id, gtask.get("updated"))


def _push_updates(list_id: str) -> None:
    for link in db.get_all_links():
        task = db.get_task(link["task_id"])
        if task is None:
            continue
        if task["updated_at"] <= link["last_synced_at"]:
            continue
        result = gt.update_task(
            list_id, link["google_task_id"], title=task["title"],
            notes=task["description"], completed=task["status"] == "done",
        )
        db.touch_google_link(task["id"], result.get("updated"))


def _pull_changes(list_id: str) -> None:
    for gtask in gt.list_tasks(list_id):
        link = db.get_link_by_google_id(gtask["id"])
        google_updated = gtask.get("updated")
        completed = gtask.get("status") == "completed"
        title = gtask.get("title") or "(untitled)"
        notes = gtask.get("notes")

        if link is None:
            task_id = db.create_task(title, notes, "google_tasks", None)
            if completed:
                db.update_task(task_id, status="done")
            db.link_google_task(task_id, gtask["id"], list_id, google_updated)
            flows.start_classification(task_id, None)
            continue

        if google_updated and link["google_updated"] and google_updated <= link["google_updated"]:
            continue  # unchanged on Google's side since last sync

        task = db.get_task(link["task_id"])
        if task is None:
            continue
        db.update_task(
            task["id"], title=title, description=notes,
            status="done" if completed else task["status"],
        )
        db.touch_google_link(task["id"], google_updated)


def run_sync() -> None:
    try:
        list_id = _get_list_id()
        _push_new(list_id)
        _push_updates(list_id)
        _pull_changes(list_id)
    except Exception as e:
        print(f"  google sync error: {e}")
