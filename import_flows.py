"""Bulk-import flows: the claude.ai (Fable) round-trips.

/import → paste (or upload) Fable's PROJECT:/checkbox grouping → sequential
per-project review cards ([✓ Import] [🗑 Discard], reply-to-edit) → parent
task + subtasks per project via ingest.import_project.

/priorities → paste (or upload) Fable's prioritized "#id" list → absolute
priorities applied in order of appearance.

Raw script_data scrape uploads (tasks/keep JSON documents) are also handled
here: they generate the grouping prompt and send it back as a document, so
the whole loop can run from a phone.

Single-user pending state, "imp" callback prefix — same conventions as
storage_flows.py.
"""
from __future__ import annotations

import json

import ai
import db
import ingest
import telegram_api as tg

_state: dict | None = None


def active() -> bool:
    return _state is not None


def cancel() -> str | None:
    global _state
    if _state is None:
        return None
    _state = None
    return "Import flow cancelled."


def wants_text() -> bool:
    return _state is not None and _state["step"] in ("await_grouped", "await_priorities")


def wants_reply(reply_to_message_id: int) -> bool:
    return (_state is not None and _state["step"] == "review"
            and reply_to_message_id == _state.get("card_message_id"))


# ── flow starts ──────────────────────────────────────────────────────────

def start_import(thread_id: int | None) -> None:
    global _state
    _state = {"step": "await_grouped", "thread_id": thread_id}
    tg.send_message(
        "Paste Fable's grouped output (the PROJECT:/checkbox reply), or upload it as a "
        "file. (/cancel to stop)",
        thread_id,
    )


def start_priorities(thread_id: int | None) -> None:
    global _state
    _state = {"step": "await_priorities", "thread_id": thread_id}
    tg.send_message(
        "Paste Fable's prioritized list (or upload it as a file) — I'll read the #ids "
        "in order. (/cancel to stop)",
        thread_id,
    )


# ── pasted text / uploaded documents ─────────────────────────────────────

def handle_text(text: str, thread_id: int | None) -> None:
    if _state["step"] == "await_grouped":
        _receive_grouped(text, thread_id)
    elif _state["step"] == "await_priorities":
        _receive_priorities(text, thread_id)


def looks_like_grouped_output(text: str) -> bool:
    lines = text.splitlines()
    return (any(ingest._PROJECT_RE.match(l) for l in lines)
            and any(ingest._ITEM_RE.match(l) for l in lines))


def start_review_from_text(text: str, thread_id: int | None) -> None:
    """Entry for auto-detected PROJECT: pastes (no /import needed)."""
    global _state
    _state = {"step": "await_grouped", "thread_id": thread_id}
    _receive_grouped(text, thread_id)


def _receive_grouped(text: str, thread_id: int | None) -> None:
    global _state
    projects = ingest.parse_grouped_output(text)
    if not projects:
        tg.send_message(
            "Couldn't find any PROJECT:/checkbox lines in that. Paste Fable's reply "
            "verbatim, or /cancel.",
            thread_id,
        )
        return
    n_items = sum(len(p["items"]) for p in projects)
    _state = {"step": "review", "projects": projects, "idx": 0, "thread_id": thread_id,
              "created": 0, "skipped": 0, "discarded": 0}
    tg.send_message(f"Parsed {len(projects)} projects, {n_items} items. Reviewing one at a time:",
                     thread_id)
    _show_review_card(thread_id)


def _receive_priorities(text: str, thread_id: int | None) -> None:
    global _state
    ordered = ingest.parse_priority_output(text)
    if not ordered:
        tg.send_message("No #ids found in that — paste the numbered list with #ids, or /cancel.",
                         thread_id)
        return
    applied = ingest.apply_priorities(ordered)
    _state = None
    top = db.get_tasks_by_priority(limit=1)
    top_line = f"\nUp first: #{top[0]['id']} {top[0]['title']}" if top else ""
    tg.send_message(f"✅ Priorities set for {applied} tasks.{top_line}\n/next any time.", thread_id)


# ── review cards ─────────────────────────────────────────────────────────

def _show_review_card(thread_id: int | None) -> None:
    global _state
    if _state["idx"] >= len(_state["projects"]):
        _finish_review(thread_id)
        return
    p = _state["projects"][_state["idx"]]
    header = f"Project {_state['idx'] + 1}/{len(_state['projects'])}: {p['name']}"
    lines = [header]
    for it in p["items"]:
        box = "☑" if it["completed"] else "☐"
        lines.append(f"{box} {it['title']}")
    lines.append("\nReply to this message to edit (e.g. \"drop 2, rename project Garage\").")
    kb = {"inline_keyboard": [[
        {"text": "✓ Import", "callback_data": "imp:ok"},
        {"text": "🗑 Discard", "callback_data": "imp:skip"},
        {"text": "Stop", "callback_data": "imp:stop"},
    ]]}
    msg_id = tg.send_message("\n".join(lines), thread_id, reply_markup=kb)
    _state["card_message_id"] = msg_id


def handle_reply(text: str, thread_id: int | None) -> None:
    p = _state["projects"][_state["idx"]]
    try:
        corrected = ai.correct_import_project(p, text)
    except Exception as e:
        tg.send_message(f"Couldn't apply that: {e}", thread_id)
        return
    _state["projects"][_state["idx"]] = corrected
    _show_review_card(thread_id)


def handle_callback(cq: dict, parts: list[str]) -> None:
    global _state
    action = parts[1]
    thread_id = cq["message"].get("message_thread_id")

    if _state is None or _state.get("step") != "review":
        tg.answer_callback_query(cq["id"], "This review has expired — /import to restart.")
        return

    p = _state["projects"][_state["idx"]]

    if action == "ok":
        result = ingest.import_project(p)
        _state["created"] += result["created"]
        _state["skipped"] += result["skipped"]
        tg.edit_message(cq["message"]["message_id"],
                         f"✅ {p['name']}: imported {result['created']} "
                         f"({result['skipped']} duplicates skipped)")
    elif action == "skip":
        _state["discarded"] += len(p["items"])
        tg.edit_message(cq["message"]["message_id"], f"🗑 Discarded \"{p['name']}\".")
    elif action == "stop":
        tg.edit_message(cq["message"]["message_id"],
                         f"Stopped. Imported so far: {_state['created']} "
                         f"(+{_state['skipped']} dupes skipped). /import to run again.")
        tg.answer_callback_query(cq["id"])
        _state = None
        return

    tg.answer_callback_query(cq["id"])
    _state["idx"] += 1
    _show_review_card(thread_id)


def _finish_review(thread_id: int | None) -> None:
    global _state
    s = _state
    _state = None
    tg.send_message(
        f"Import complete: {s['created']} tasks created, {s['skipped']} duplicates skipped, "
        f"{s['discarded']} discarded.\n"
        "They'll sync to Google Tasks on the next cycle. Next step: /export → paste into "
        "claude.ai → /priorities with the reply.",
        thread_id,
    )


# ── raw scrape documents (generate the grouping prompt on the bot) ───────

def handle_scrape_json(payload: dict, thread_id: int | None) -> bool:
    """If the uploaded JSON is a script_data scrape, build the grouping
    prompt and send it back as a document. Returns True if handled."""
    if "lists" in payload and "tasks.google" in str(payload.get("source", "")):
        items = ingest.clean_from_parsed(payload)
        prompt = ingest.build_grouping_prompt(items, [])
        tg.send_document(prompt, "grouping_prompt.txt", thread_id=thread_id,
                          caption=f"Cleaned {len(items)} tasks. Paste this into claude.ai, "
                                  "then /import with the reply. (Keep-notes filtering runs "
                                  "separately — upload the keep JSON too if you want it.)")
        return True
    if "notes" in payload:
        notes = [{"file": f"note{i}", "title": n.get("title", ""), "body": (n.get("body") or "")[:600]}
                 for i, n in enumerate(payload["notes"]) if (n.get("body") or "").strip()
                 and n.get("body") != "Take a note…"]
        tg.send_message(f"Filtering {len(notes)} Keep notes for actionable items — this takes "
                         "a minute…", thread_id)
        found = ingest.filter_keep_actionable(notes)
        prompt = ingest.build_grouping_prompt([], found)
        tg.send_document(prompt, "grouping_prompt_keep.txt", thread_id=thread_id,
                          caption=f"Found {len(found)} actionable items in {len(notes)} notes. "
                                  "Paste into claude.ai, then /import with the reply.")
        return True
    return False
