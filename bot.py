#!/usr/bin/env python3
"""Semantic Task Manager — Phase 0 (capture).

Send text, a screenshot of a list, a photo of a situation, or a photo of a
recipe/instructions to the bot's Telegram group; it saves tasks to SQLite.
Reply to the bot's confirmation message with natural-language corrections
("delete the second one", "merge 1 and 3") to edit them.

.env keys: STM_BOT_ID, STM_CHAT_ID, STM_IS_FORUM, ANTHROPIC_API_KEY

Run: python bot.py
"""
from __future__ import annotations

import time

import ai
import config
import db
import flows
import telegram_api as tg

_offset = 0


def row_to_task_dict(row) -> dict:
    return {"id": row["id"], "title": row["title"], "description": row["description"]}


# ── capture handlers ─────────────────────────────────────────────────────

def handle_text_capture(text: str, message_id: int, thread_id: int | None) -> None:
    capture_id = db.create_capture(config.CHAT_ID, thread_id, message_id, "text", raw_text=text)
    try:
        parsed = ai.parse_text_to_tasks(text)
    except Exception as e:
        tg.send_message(f"Couldn't parse that: {e}", thread_id)
        return
    if not parsed:
        tg.send_message("Didn't find a task in that message.", thread_id)
        return

    task_ids = [
        db.create_task(t["title"], t.get("description"), "text", message_id)
        for t in parsed
    ]
    _send_confirmation(capture_id, task_ids, config.MODEL_HAIKU, {"tasks": parsed}, thread_id)
    for task_id in task_ids:
        flows.start_classification(task_id, thread_id)


def handle_photo_capture(msg: dict, thread_id: int | None) -> None:
    photo_sizes = msg["photo"]
    file_id = photo_sizes[-1]["file_id"]  # largest resolution
    message_id = msg["message_id"]
    capture_id = db.create_capture(config.CHAT_ID, thread_id, message_id, "photo", image_file_id=file_id)

    image_bytes = tg.download_file(file_id)
    if image_bytes is None:
        tg.send_message("Couldn't download that photo.", thread_id)
        return

    try:
        result = ai.classify_photo(image_bytes, "image/jpeg")
    except Exception as e:
        tg.send_message(f"Couldn't read that photo: {e}", thread_id)
        return

    kind = result.get("kind")
    if kind == "list":
        tasks = result.get("tasks", [])
        if not tasks:
            tg.send_message("Didn't find any list items in that screenshot.", thread_id)
            return
        task_ids = [db.create_task(t["title"], t.get("description"), "screenshot", message_id) for t in tasks]
        _send_confirmation(capture_id, task_ids, config.MODEL_SONNET, result, thread_id)
        for task_id in task_ids:
            flows.start_classification(task_id, thread_id)

    elif kind == "situation":
        candidates = result.get("candidates", [])
        if not candidates:
            tg.send_message("Couldn't tell what tasks that implies — try describing it in text.", thread_id)
            return
        keyboard = tg.candidates_keyboard(capture_id, candidates)
        bot_msg_id = tg.send_message("What would you like to do?", thread_id, reply_markup=keyboard)
        db.finalize_capture(capture_id, config.MODEL_SONNET, result, [], bot_msg_id or 0)

    elif kind == "procedure":
        task_title = result.get("task_title") or result.get("title") or "Untitled procedure"
        task_id = db.create_task(task_title, None, "photo", message_id)
        db.create_procedure(
            task_id,
            result.get("procedure_type", "instructions"),
            result.get("title"),
            {"ingredients": result.get("ingredients", []), "steps": result.get("steps", [])},
        )
        _send_confirmation(capture_id, [task_id], config.MODEL_SONNET, result, thread_id)
        flows.start_classification(task_id, thread_id)

    else:
        tg.send_message("Couldn't classify that photo.", thread_id)


def handle_other_reply(capture_row, text: str, thread_id: int | None) -> None:
    capture_id = capture_row["id"]
    task_id = db.create_task(text, None, "photo", capture_row["inbound_message_id"])
    _send_confirmation(capture_id, [task_id], config.MODEL_SONNET, {"other": text}, thread_id)
    flows.start_classification(task_id, thread_id)


def handle_callback_query(cq: dict) -> None:
    data = cq.get("data", "")
    if data == "noop":
        tg.answer_callback_query(cq["id"])
        return
    parts = data.split(":")
    prefix = parts[0]

    if prefix == "clsf":
        flows.handle_classification_callback(cq, int(parts[1]), parts[2], parts[3])
        return
    if prefix == "skill":
        flows.handle_skill_callback(cq, int(parts[1]), parts[2])
        return
    if prefix == "newent":
        flows.handle_newent_callback(cq, int(parts[1]), int(parts[2]))
        return
    if prefix == "chk":
        flows.handle_checklist_toggle(cq, int(parts[1]), int(parts[2]))
        return
    if prefix == "chkdone":
        flows.handle_checklist_done(cq, int(parts[1]))
        return
    if prefix == "sub":
        flows.handle_substitution_callback(cq, int(parts[1]), int(parts[2]), parts[3])
        return
    if prefix != "sit":
        return

    capture_id, choice = int(parts[1]), parts[2]
    capture_row = db.get_capture(capture_id)
    if capture_row is None:
        tg.answer_callback_query(cq["id"], "Expired.")
        return
    thread_id = capture_row["thread_id"]

    if choice == "other":
        prompt_msg_id = tg.send_message("Reply to this message with your task.", thread_id)
        db.set_awaiting_other(capture_id, prompt_msg_id or 0)
        tg.answer_callback_query(cq["id"])
        return

    import json
    candidates = json.loads(capture_row["ai_response_json"] or "{}").get("candidates", [])
    idx = int(choice)
    if idx >= len(candidates):
        tg.answer_callback_query(cq["id"], "Invalid choice.")
        return
    title = candidates[idx]
    task_id = db.create_task(title, None, "photo", capture_row["inbound_message_id"])
    _send_confirmation(capture_id, [task_id], config.MODEL_SONNET,
                        json.loads(capture_row["ai_response_json"]), thread_id)
    tg.answer_callback_query(cq["id"], f"Saved: {title}")
    flows.start_classification(task_id, thread_id)


def handle_correction(capture_row, instruction: str, thread_id: int | None) -> None:
    import json
    task_ids = json.loads(capture_row["linked_task_ids"] or "[]")
    rows = db.get_tasks(task_ids)
    if not rows:
        tg.send_message("No linked tasks found to correct.", thread_id)
        return
    tasks = [row_to_task_dict(r) for r in rows]

    try:
        ops = ai.apply_correction(tasks, instruction)
    except Exception as e:
        tg.send_message(f"Couldn't apply that correction: {e}", thread_id)
        return

    surviving = set(task_ids)
    for edit in ops.get("edit", []):
        db.update_task(edit["id"], title=edit["title"], description=edit.get("description"))
    for tid in ops.get("delete", []):
        db.delete_task(tid)
        surviving.discard(tid)
    for merge in ops.get("merge", []):
        ids = merge["ids"]
        keep_id = ids[0]
        db.update_task(keep_id, title=merge["title"], description=merge.get("description"))
        for tid in ids[1:]:
            db.delete_task(tid)
            surviving.discard(tid)
    new_ids = []
    for add in ops.get("add", []):
        new_ids.append(db.create_task(add["title"], add.get("description"), "text", None))

    final_ids = list(surviving) + new_ids
    _send_confirmation(capture_row["id"], final_ids, config.MODEL_HAIKU, ops, thread_id, prefix="Updated:\n")
    for task_id in new_ids:
        flows.start_classification(task_id, thread_id)


def _send_confirmation(capture_id: int, task_ids: list[int], model_used: str,
                        ai_response: dict, thread_id: int | None, prefix: str = "Saved:\n") -> None:
    rows = db.get_tasks(task_ids)
    lines = []
    for r in rows:
        line = f"#{r['id']} {r['title']}"
        if r["description"]:
            line += f"\n    ↳ {r['description']}"
        lines.append(line)
    text = prefix + ("\n".join(lines) if lines else "(nothing)")
    bot_msg_id = tg.send_message(text, thread_id)
    db.finalize_capture(capture_id, model_used, ai_response, task_ids, bot_msg_id or 0)


# ── command handling ─────────────────────────────────────────────────────

def handle_command(text: str, thread_id: int | None) -> None:
    parts = text.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    args = parts[1:]

    if cmd == "help":
        tg.send_message(
            "Semantic Task Manager (Phase 0 + 1a)\n\n"
            "Send text, a screenshot of a list, a photo of a situation, or a photo of a "
            "recipe/instructions — I'll save it as one or more tasks, then walk through "
            "urgency/interest/energy/value, skill level, and a supply checklist.\n"
            "Reply to my confirmation message with corrections in plain English "
            "(\"delete the second one\", \"merge 1 and 3\").\n\n"
            "/tasks — list all open tasks\n"
            "/delete <id> — delete a task by id\n"
            "/store — supply-store shopping list\n"
            "/online — online shopping list\n"
            "/groceries — grocery list\n"
            "/help — show this message",
            thread_id,
        )
    elif cmd == "tasks":
        rows = db.get_all_tasks()
        if not rows:
            tg.send_message("No open tasks.", thread_id)
            return
        lines = ["Open tasks:"]
        for r in rows:
            tag = "✓ classified" if r["classification_complete"] else "unclassified"
            lines.append(f"#{r['id']} {r['title']} ({tag})")
        tg.send_message("\n".join(lines), thread_id)
    elif cmd == "delete":
        if not args or not args[0].isdigit():
            tg.send_message("Usage: /delete <task_id>", thread_id)
            return
        db.delete_task(int(args[0]))
        tg.send_message(f"Deleted #{args[0]}.", thread_id)
    elif cmd in ("store", "online", "groceries"):
        list_name = {"store": db.SUPPLY_STORE_LIST, "online": db.ONLINE_LIST,
                     "groceries": db.GROCERY_LIST}[cmd]
        items = db.get_shopping_list_items(list_name)
        if not items:
            tg.send_message(f"{list_name}: nothing on the list.", thread_id)
            return
        # One row per (entity, task) — the same supply can be required by
        # multiple tasks, which is correct for tracking, but should collapse
        # to one line here rather than repeating the name per task.
        names = list(dict.fromkeys(i["canonical_name"] for i in items))
        lines = [f"{list_name}:"] + [f"- {n}" for n in names]
        tg.send_message("\n".join(lines), thread_id)


# ── main loop ─────────────────────────────────────────────────────────────

def process_update(update: dict) -> None:
    global _offset
    _offset = max(_offset, update["update_id"] + 1)

    if "callback_query" in update:
        handle_callback_query(update["callback_query"])
        return

    msg = update.get("message")
    if not msg or msg.get("chat", {}).get("id") != config.CHAT_ID:
        return

    thread_id = msg.get("message_thread_id")
    text = msg.get("text", "").strip()
    reply_to = msg.get("reply_to_message")

    if text.startswith("/"):
        handle_command(text, thread_id)
        return

    if reply_to:
        reply_id = reply_to["message_id"]
        cap = db.find_capture_by_awaiting_other(reply_id)
        if cap is not None:
            handle_other_reply(cap, text, thread_id)
            return
        cap = db.find_capture_by_bot_message(reply_id)
        if cap is not None:
            handle_correction(cap, text, thread_id)
            return

    if msg.get("photo"):
        handle_photo_capture(msg, thread_id)
    elif text:
        handle_text_capture(text, msg["message_id"], thread_id)


def main() -> None:
    global _offset

    if not config.TELEGRAM_TOKEN:
        raise ValueError("STM_BOT_ID not set in .env")
    if not config.CHAT_ID:
        raise ValueError("STM_CHAT_ID not set in .env")
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    db.init()
    print("Semantic task manager started (Phase 0 capture).")

    while True:
        updates = tg.get_updates(_offset)
        for update in updates:
            try:
                process_update(update)
            except Exception as e:
                print(f"  error processing update: {e}")
        if not updates:
            time.sleep(1)


if __name__ == "__main__":
    main()
