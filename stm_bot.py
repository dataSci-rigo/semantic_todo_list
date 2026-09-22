#!/usr/bin/env python3
"""Semantic Task Manager bot (service app-stm) + the Growth coaching layer.

Send text, a screenshot of a list, a photo of a situation, or a photo of a
recipe/instructions to the bot; it saves tasks to SQLite. Reply to the bot's
confirmation message with natural-language corrections ("delete the second
one", "merge 1 and 3") to edit them. The growth/ package adds the daily
coaching arc (see growth/__init__.py and ~/Documents/gbot/).

.env keys: STM_BOT_ID, STM_CHAT_ID, STM_IS_FORUM, ANTHROPIC_API_KEY
Optional: GROWTH_CHAT_ID, GROWTH_THREAD_ID, GROWTH_ALLOWED_USER_IDS

Run: python stm_bot.py  (one long-poller per token — never run two copies)
"""
from __future__ import annotations

import argparse
import html
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import ai
import config
import context
import db
import flows
import import_flows
import ingest
import storage_flows
import telegram_api as tg
import growth

try:
    import sync
except ImportError as e:
    sync = None
    print(f"  Google Tasks sync unavailable (missing packages: {e}) — /sync and auto-sync disabled.")

_offset = 0
_last_sync = 0.0
_last_window_check = 0.0
_TZ = ZoneInfo(config.TIMEZONE)
DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def esc(s) -> str:
    """Escape dynamic text for parse_mode=HTML messages."""
    return html.escape(str(s), quote=False)


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
    flows.start_classification_batch(task_ids, thread_id)


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
        flows.start_classification_batch(task_ids, thread_id)

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
    if prefix == "stor":
        storage_flows.handle_callback(cq, parts)
        return
    if prefix == "task":
        _handle_task_callback(cq, parts)
        return
    if prefix == "undo":
        _handle_undo_callback(cq, parts)
        return
    if prefix == "clsfstart":
        thread_id = cq["message"].get("message_thread_id")
        pending = [r["id"] for r in db.get_all_tasks() if not r["classification_complete"]]
        tg.answer_callback_query(cq["id"])
        if pending:
            flows.start_classification_batch(pending, thread_id)
        return
    if prefix == "imp":
        import_flows.handle_callback(cq, parts)
        return
    if prefix == "gb":
        growth.handle_callback(cq, parts)
        return
    if prefix == "next":
        thread_id = cq["message"].get("message_thread_id")
        tg.answer_callback_query(cq["id"])
        _show_next(int(parts[1]), thread_id, edit_message_id=cq["message"]["message_id"])
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
    flows.start_classification_batch(new_ids, thread_id)


def _send_confirmation(capture_id: int, task_ids: list[int], model_used: str,
                        ai_response: dict, thread_id: int | None, prefix: str = "Saved:\n") -> None:
    rows = db.get_tasks(task_ids)
    lines = []
    for r in rows:
        line = f"<code>#{r['id']}</code> <b>{esc(r['title'])}</b>"
        if r["description"]:
            line += f"\n    ↳ {esc(r['description'])}"
        lines.append(line)
    text = esc(prefix) + ("\n".join(lines) if lines else "(nothing)")
    text += "\n\n<i>Reply to this message to correct.</i>"
    kb = {"inline_keyboard": [[{"text": "↩️ Undo", "callback_data": f"undo:cap:{capture_id}"}]]} \
        if task_ids else None
    bot_msg_id = tg.send_message(text, thread_id, reply_markup=kb, parse_mode="HTML")
    db.finalize_capture(capture_id, model_used, ai_response, task_ids, bot_msg_id or 0)


DIMENSIONS = ("urgency", "interest", "energy", "value")


def _task_line(r) -> str:
    cat = f" <i>[{esc(r['category'])}]</i>" if r["category"] else ""
    if r["classification_complete"] and r["urgency"] is not None:
        meta = f" <i>u{r['urgency']}/i{r['interest']}/e{r['energy']}/v{r['value']}</i>"
    elif r["classification_complete"]:
        meta = ""
    else:
        meta = " <i>(unclassified)</i>"
    return f"<code>#{r['id']}</code> <b>{esc(r['title'])}</b>{cat}{meta}"


def _task_buttons(rows, cap: int = 30) -> dict | None:
    if not rows:
        return None
    btns = [{"text": f"#{r['id']} {r['title'][:28]}", "callback_data": f"task:{r['id']}"}
            for r in rows[:cap]]
    per_row = 2 if len(btns) > 8 else 1
    return {"inline_keyboard": [btns[i:i + per_row] for i in range(0, len(btns), per_row)]}


def _send_task_list(rows, header: str, thread_id: int | None, cap: int = 30) -> None:
    """Flat (rank-preserving) list with tap-to-open buttons."""
    if not rows:
        tg.send_message(f"{header} (none)", thread_id)
        return
    lines = [f"<b>{esc(header)}</b>"] + [_task_line(r) for r in rows[:cap]]
    if len(rows) > cap:
        lines.append(f"<i>…and {len(rows) - cap} more</i>")
    tg.send_message("\n".join(lines), thread_id, reply_markup=_task_buttons(rows), parse_mode="HTML")


def _build_task_hub() -> tuple[str, dict | None]:
    """Grouped-by-category overview of TOP-LEVEL tasks (projects show a
    subtask count; their subtasks live on the project's card). Capped so a
    300-task import can't blow Telegram's message/button limits."""
    rows = [r for r in db.get_all_tasks() if not r["parent_task_id"]]
    if not rows:
        return "No open tasks. Send me anything to capture one.", None
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r["category"] or "Uncategorized", []).append(r)
    lines = ["<b>Your tasks</b>"]
    shown = 0
    for cat in sorted(groups, key=lambda c: (c == "Uncategorized", c.lower())):
        lines.append(f"\n<u>{esc(cat)}</u>")
        for r in groups[cat]:
            subs = db.get_subtasks(r["id"])
            open_subs = [s for s in subs if s["status"] not in ("done", "dropped")]
            suffix = f" — {len(open_subs)}/{len(subs)} open subtasks" if subs else ""
            lines.append(_task_line(r) + esc(suffix))
            shown += 1
        if sum(len(l) for l in lines) > 3500:
            remaining = len(rows) - shown
            if remaining > 0:
                lines.append(f"\n<i>…and {remaining} more — /tasks &lt;category&gt; to filter.</i>")
            break
    lines.append("\n<i>Tap a task to manage it.</i>")
    return "\n".join(lines), _task_buttons(rows)


def _send_task_hub(thread_id: int | None) -> None:
    text, kb = _build_task_hub()
    tg.send_message(text, thread_id, reply_markup=kb, parse_mode="HTML")


def _build_task_card(task_id: int, confirm_delete: bool = False) -> tuple[str, dict] | None:
    r = db.get_task(task_id)
    if r is None:
        return None
    status_icon = {"done": "✅", "dropped": "🗑"}.get(r["status"], "▫️")
    lines = [f"{status_icon} <code>#{r['id']}</code> <b>{esc(r['title'])}</b>"]
    if r["description"]:
        lines.append(esc(r["description"]))
    meta = []
    if r["category"]:
        meta.append(f"category: {esc(r['category'])}")
    if r["classification_complete"] and r["urgency"] is not None:
        meta.append(f"u{r['urgency']}/i{r['interest']}/e{r['energy']}/v{r['value']}")
    if r["skill_level_required"] is not None:
        meta.append(f"skill {r['skill_level_required']}")
    if r["estimated_active_minutes"] is not None:
        est = f"{r['estimated_active_minutes']} min active"
        if r["estimated_passive_minutes"]:
            est += f" + {r['estimated_passive_minutes']} passive"
        meta.append(est)
    if meta:
        lines.append("<i>" + " · ".join(meta) + "</i>")
    reqs = db.get_requirements_for_task(task_id)
    if reqs:
        owned = sum(1 for q in reqs if q["satisfied"])
        lines.append(f"<i>supplies: {owned}/{len(reqs)} ready</i>")
    subs = db.get_subtasks(task_id)
    if subs:
        lines.append("")
        for s in subs[:12]:
            box = "☑" if s["status"] == "done" else "☐"
            lines.append(f"{box} <code>#{s['id']}</code> {esc(s['title'])}")
        if len(subs) > 12:
            lines.append(f"<i>…and {len(subs) - 12} more</i>")
        lines.append("<i>mark subtasks: \"12 is done\" or /done 12</i>")

    delete_btn = ({"text": "🗑 Really delete?", "callback_data": f"task:{task_id}:del2"}
                  if confirm_delete else
                  {"text": "🗑 Delete", "callback_data": f"task:{task_id}:del1"})
    kb_rows = []
    if r["status"] != "done":
        kb_rows.append([{"text": "✓ Done", "callback_data": f"task:{task_id}:done"}, delete_btn])
    else:
        kb_rows.append([delete_btn])
    kb_rows.append([{"text": "📦 Supplies", "callback_data": f"task:{task_id}:sup"},
                    {"text": "✏️ Category", "callback_data": f"task:{task_id}:cat"}])
    kb_rows.append([{"text": "⬅ Back", "callback_data": "task:hub"}])
    return "\n".join(lines), {"inline_keyboard": kb_rows}


def _mark_done_with_undo(task_id: int, thread_id: int | None) -> None:
    task = db.get_task(task_id)
    if task is None:
        tg.send_message(f"No task #{task_id}.", thread_id)
        return
    prior = task["status"]
    db.update_task(task_id, status="done")
    kb = {"inline_keyboard": [[{"text": "↩️ Undo", "callback_data": f"undo:done:{task_id}:{prior}"}]]}
    tg.send_message(f"✅ #{task_id} {task['title']} — done", thread_id, reply_markup=kb)
    _notify_unblocked(task_id, thread_id)


def _show_next(offset: int, thread_id: int | None, edit_message_id: int | None = None) -> None:
    rows = db.get_tasks_by_priority(limit=offset + 1)
    if not rows:
        tg.send_message("No open tasks — nice.", thread_id)
        return
    if offset >= len(rows):
        offset = 0  # wrap around
    r = rows[offset]
    card = _build_task_card(r["id"])
    if card is None:
        return
    pr = f"priority {r['priority']}" if r["priority"] else "unprioritized — score-ranked; /export to prioritize"
    text = f"🎯 <b>Next up</b> <i>({esc(pr)})</i>\n\n" + card[0]
    kb = card[1]
    kb["inline_keyboard"].append([{"text": "⏭ Skip", "callback_data": f"next:{offset + 1}"}])
    if edit_message_id:
        tg.edit_message(edit_message_id, text, reply_markup=kb, parse_mode="HTML")
    else:
        tg.send_message(text, thread_id, reply_markup=kb, parse_mode="HTML")


def _handle_document(msg: dict, thread_id: int | None) -> None:
    import json as _json
    doc = msg["document"]
    if (doc.get("file_size") or 0) > 5 * 1024 * 1024:
        tg.send_message("That file is too big (5MB max).", thread_id)
        return
    data = tg.download_file(doc["file_id"])
    if data is None:
        tg.send_message("Couldn't download that file.", thread_id)
        return
    text = data.decode("utf-8", errors="replace")

    if import_flows.wants_text():
        import_flows.handle_text(text, thread_id)
        return
    try:
        payload = _json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, dict) and import_flows.handle_scrape_json(payload, thread_id):
        return
    if import_flows.looks_like_grouped_output(text):
        import_flows.start_review_from_text(text, thread_id)
        return
    tg.send_message(
        "Couldn't recognize that file — I take script_data scrape JSONs, Fable's "
        "PROJECT:/checkbox groupings, or prioritized #id lists (after /priorities).",
        thread_id,
    )


def _handle_task_callback(cq: dict, parts: list[str]) -> None:
    message_id = cq["message"]["message_id"]
    thread_id = cq["message"].get("message_thread_id")

    if parts[1] == "hub":
        text, kb = _build_task_hub()
        tg.edit_message(message_id, text, reply_markup=kb or {"inline_keyboard": []}, parse_mode="HTML")
        tg.answer_callback_query(cq["id"])
        return

    task_id = int(parts[1])
    action = parts[2] if len(parts) > 2 else "open"

    if action in ("open", "del1"):
        card = _build_task_card(task_id, confirm_delete=(action == "del1"))
        if card is None:
            tg.answer_callback_query(cq["id"], "Task not found.")
            return
        tg.edit_message(message_id, card[0], reply_markup=card[1], parse_mode="HTML")
        tg.answer_callback_query(cq["id"])

    elif action == "done":
        db.update_task(task_id, status="done")
        card = _build_task_card(task_id)
        if card:
            tg.edit_message(message_id, card[0], reply_markup=card[1], parse_mode="HTML")
        tg.answer_callback_query(cq["id"], "Done ✓")
        _notify_unblocked(task_id, thread_id)

    elif action == "del2":
        db.delete_task(task_id)
        tg.edit_message(message_id, f"🗑 Deleted #{task_id}.")
        tg.answer_callback_query(cq["id"])

    elif action == "sup":
        reqs = db.get_requirements_for_task(task_id)
        if not reqs:
            tg.answer_callback_query(cq["id"], "No supplies listed for this task.")
            return
        lines = [f"📦 Supplies for <code>#{task_id}</code>:"]
        for q in reqs:
            icon = "✅" if q["satisfied"] else "🛒"
            line = f"{icon} {esc(q['canonical_name'])}"
            s = db.get_item_storage(q["entity_id"])
            if s is not None:
                if s["box_number"] is not None:
                    line += f" — Box #{s['box_number']} @ {esc(s['location_name'] or '?')}"
                elif s["location_name"]:
                    line += f" — {esc(s['location_name'])}"
            lines.append(line)
        lines.append("<i>/find &lt;item&gt; for photos</i>")
        tg.send_message("\n".join(lines), thread_id, parse_mode="HTML")
        tg.answer_callback_query(cq["id"])

    elif action == "cat":
        cats = db.get_distinct_categories()
        rows = [[{"text": c, "callback_data": f"task:{task_id}:setcat:{i}"}]
                for i, c in enumerate(cats)]
        rows.append([{"text": "⬅ Back", "callback_data": f"task:{task_id}"}])
        tg.edit_message(message_id,
                         f"Pick a category for #{task_id} (or type /category {task_id} <new name>):",
                         reply_markup={"inline_keyboard": rows})
        tg.answer_callback_query(cq["id"])

    elif action == "setcat":
        cats = db.get_distinct_categories()
        idx = int(parts[3])
        if idx < len(cats):
            db.update_task(task_id, category=cats[idx])
        card = _build_task_card(task_id)
        if card:
            tg.edit_message(message_id, card[0], reply_markup=card[1], parse_mode="HTML")
        tg.answer_callback_query(cq["id"], "Category set.")


def _handle_undo_callback(cq: dict, parts: list[str]) -> None:
    import json
    message_id = cq["message"]["message_id"]

    if parts[1] == "cap":
        cap = db.get_capture(int(parts[2]))
        if cap is None:
            tg.answer_callback_query(cq["id"], "Nothing to undo.")
            return
        task_ids = json.loads(cap["linked_task_ids"] or "[]")
        for tid in task_ids:
            db.delete_task(tid)
        tg.edit_message(message_id, f"↩️ Undone — {len(task_ids)} task(s) removed.")
        tg.answer_callback_query(cq["id"])

    elif parts[1] == "done":
        task_id = int(parts[2])
        prior = parts[3] if len(parts) > 3 else "draft"
        db.update_task(task_id, status=prior)
        tg.edit_message(message_id, f"↩️ #{task_id} is open again.")
        tg.answer_callback_query(cq["id"])

    elif parts[1] == "merge":
        task_id = int(parts[2])
        if sync is not None and sync.revert_merge(task_id):
            tg.edit_message(message_id, f"↩️ #{task_id} restored to your pre-merge version "
                                         "(pushes on next sync).")
        else:
            tg.edit_message(message_id, "Nothing to revert — that merge is no longer held.")
        tg.answer_callback_query(cq["id"])


def _handle_tasks_command(args: list[str], thread_id: int | None) -> None:
    if not args:
        _send_task_hub(thread_id)
        return
    if args[0].lower() in DIMENSIONS:
        dim = args[0].lower()
        _send_task_list(db.get_all_tasks(order_by=dim), f"Open tasks by {dim} (high to low):", thread_id)
        return
    if args[0].lower() == "categories":
        cats = db.get_distinct_categories()
        if not cats:
            tg.send_message("No categories yet.", thread_id)
            return
        tg.send_message("Categories:\n" + "\n".join(f"- {c}" for c in cats), thread_id)
        return
    category = " ".join(args)
    _send_task_list(db.get_all_tasks(category=category), f"Tasks in \"{category}\":", thread_id)


def _handle_dependency_command(args: list[str], thread_id: int | None) -> None:
    if len(args) < 2 or not args[0].isdigit():
        tg.send_message(
            "Usage: /dependency <task_id> after <other_task_id>  — or —  "
            "/dependency <task_id> <free-text constraint>",
            thread_id,
        )
        return
    task_id = int(args[0])
    rest = args[1:]
    if rest[0].lower() == "after" and len(rest) >= 2 and rest[1].isdigit():
        other_id = int(rest[1])
        db.create_dependency(task_id, other_id, "finish-to-start")
        tg.send_message(f"#{task_id} now depends on #{other_id} finishing first.", thread_id)
    else:
        note = " ".join(rest)
        db.create_dependency(task_id, None, "constraint", note=note)
        tg.send_message(f"Constraint added to #{task_id}: {note}", thread_id)


def _handle_window_command(text: str, thread_id: int | None) -> None:
    try:
        parsed = ai.parse_window(text)
    except Exception as e:
        tg.send_message(f"Couldn't parse that window: {e}", thread_id)
        return
    duration = parsed.get("duration_minutes")
    if not duration:
        tg.send_message(
            "Couldn't figure out how much time that gives you — try including a number of minutes.",
            thread_id,
        )
        return
    location = parsed.get("location")
    notes = parsed.get("notes")
    db.create_availability_window(duration, location, notes, text)

    rows = context.eligible_tasks(duration, location)
    header = f"Eligible for {duration} min"
    if location:
        header += f" at {location}"
    header += ":"
    _send_task_list(rows, header, thread_id)


def _handle_recurring_command(args: list[str], thread_id: int | None) -> None:
    if not args:
        tg.send_message(
            "Usage: /recurring <description> — e.g. /recurring every Sunday 9am for 4 hours, at home\n"
            "/recurring list — show saved recurring windows\n"
            "/recurring delete <id> — remove one",
            thread_id,
        )
        return

    if args[0].lower() == "list":
        rows = db.get_recurring_windows()
        if not rows:
            tg.send_message("No recurring windows saved.", thread_id)
            return
        lines = ["Recurring windows:"]
        for r in rows:
            loc = f" at {r['location']}" if r["location"] else ""
            lines.append(
                f"#{r['id']} {DAY_NAMES[r['day_of_week']]} {r['start_time']} "
                f"for {r['duration_minutes']} min{loc}"
            )
        tg.send_message("\n".join(lines), thread_id)
        return

    if args[0].lower() == "delete":
        if len(args) < 2 or not args[1].isdigit():
            tg.send_message("Usage: /recurring delete <id>", thread_id)
            return
        db.delete_availability_window(int(args[1]))
        tg.send_message(f"Deleted recurring window #{args[1]}.", thread_id)
        return

    text = " ".join(args)
    try:
        parsed = ai.parse_recurring_window(text)
    except Exception as e:
        tg.send_message(f"Couldn't parse that: {e}", thread_id)
        return

    day = parsed.get("day_of_week")
    start_time = parsed.get("start_time")
    duration = parsed.get("duration_minutes")
    if day is None or not start_time or not duration:
        tg.send_message(
            "Couldn't pin down a day, start time, and duration from that — try something like "
            "\"every Sunday 9am for 4 hours\".",
            thread_id,
        )
        return

    window_id = db.create_recurring_window(
        day, start_time, duration, parsed.get("location"), parsed.get("notes"), text
    )
    tg.send_message(
        f"Saved recurring window #{window_id}: {DAY_NAMES[day]} {start_time} for {duration} min.",
        thread_id,
    )


def _send_shopping_list(list_name: str, thread_id: int | None) -> None:
    items = db.get_shopping_list_items(list_name)
    if not items:
        tg.send_message(f"🛒 {list_name}: nothing on the list.", thread_id)
        return
    # One row per (entity, task) — the same supply can be required by
    # multiple tasks, which is correct for tracking, but should collapse
    # to one line here rather than repeating the name per task.
    names = list(dict.fromkeys(i["canonical_name"] for i in items))
    lines = [f"🛒 {list_name}:"] + [f"- {n}" for n in names]
    tg.send_message("\n".join(lines), thread_id)


# ── natural-language routing ─────────────────────────────────────────────

def _route_text(text: str, message_id: int, thread_id: int | None) -> None:
    """Intent-route a free-text message; capture is the safe fallback so
    nothing is ever lost."""
    if import_flows.looks_like_grouped_output(text):
        import_flows.start_review_from_text(text, thread_id)
        return
    open_tasks = [{"id": r["id"], "title": r["title"]} for r in db.get_all_tasks()]
    try:
        route = ai.route_message(text, open_tasks)
    except Exception as e:
        print(f"  route error: {e}")
        route = {"intent": "capture"}
    intent = route.get("intent")

    if intent == "next_task":
        _show_next(0, thread_id)
        return
    if intent == "eligible_query":
        minutes = route.get("minutes")
        if not minutes:
            tg.send_message("How much time do you have? e.g. \"what can I do in 30 minutes\"", thread_id)
            return
        rows = context.eligible_tasks(int(minutes))
        _send_task_list(rows, f"🧭 Eligible for {minutes} min:", thread_id)
    elif intent == "shopping_list":
        list_map = {"store": db.SUPPLY_STORE_LIST, "online": db.ONLINE_LIST,
                    "groceries": db.GROCERY_LIST}
        chosen = route.get("list")
        if chosen in list_map:
            _send_shopping_list(list_map[chosen], thread_id)
        else:
            for name in list_map.values():
                if db.get_shopping_list_items(name):
                    _send_shopping_list(name, thread_id)
    elif intent == "find_item" and route.get("query"):
        _handle_find_command(route["query"].split(), thread_id)
    elif intent == "mark_done" and route.get("task_ids"):
        for task_id in route["task_ids"]:
            _mark_done_with_undo(int(task_id), thread_id)
    elif intent == "list_tasks":
        _send_task_hub(thread_id)
    elif intent == "correction_hint":
        tg.send_message(
            "To edit just-captured tasks, reply directly to my \"Saved:\" message with that "
            "instruction — the reply tells me which capture you mean.",
            thread_id,
        )
    else:
        handle_text_capture(text, message_id, thread_id)


# ── storage search & browse ──────────────────────────────────────────────

def _send_storage_line(entity_id: int, thread_id: int | None) -> None:
    s = db.get_item_storage(entity_id)
    if s is None:
        return
    if s["box_number"] is not None:
        where = f"Box #{s['box_number']} @ {s['location_name'] or 'location unknown'}"
    else:
        where = s["location_name"] or "location unknown"
    lines = [f"🔎 {s['canonical_name']} — {where}"]
    needed = db.get_tasks_requiring(entity_id)
    if needed:
        lines.append("needed by: " + ", ".join(f"#{t['id']} {t['title']}" for t in needed))
    tg.send_message("\n".join(lines), thread_id)
    if s["box_photo_file_id"]:
        tg.send_photo(s["box_photo_file_id"], caption=f"Box #{s['box_number']} contents", thread_id=thread_id)
    if s["location_photo_file_id"]:
        tg.send_photo(s["location_photo_file_id"], caption=s["location_name"], thread_id=thread_id)


def _handle_find_command(args: list[str], thread_id: int | None) -> None:
    if not args:
        tg.send_message("Usage: /find <what you're looking for> — or /find task <id>", thread_id)
        return

    if args[0].lower() == "task" and len(args) > 1 and args[1].isdigit():
        task_id = int(args[1])
        found = False
        for req in db.get_requirements_for_task(task_id):
            if db.get_item_storage(req["entity_id"]) is not None:
                _send_storage_line(req["entity_id"], thread_id)
                found = True
        if not found:
            tg.send_message(f"None of #{task_id}'s supplies have recorded storage yet.", thread_id)
        return

    stored = db.get_all_stored_items()
    if not stored:
        tg.send_message("Nothing stored yet — /box to catalog your first box.", thread_id)
        return
    items = [{"entity_id": r["entity_id"], "name": r["canonical_name"],
              "aliases": db.get_aliases(r["entity_id"])} for r in stored]
    query = " ".join(args)
    try:
        entity_ids = ai.match_stored_items(query, items)
    except Exception as e:
        tg.send_message(f"Search failed: {e}", thread_id)
        return
    if not entity_ids:
        tg.send_message(f"No stored items match \"{query}\".", thread_id)
        return
    for entity_id in entity_ids:
        _send_storage_line(entity_id, thread_id)


def _handle_stuff_command(args: list[str], thread_id: int | None) -> None:
    if args and args[0].lstrip("#").isdigit():
        box = db.get_box_by_number(int(args[0].lstrip("#")))
        if box is None:
            tg.send_message(f"No box #{args[0].lstrip('#')}.", thread_id)
            return
        items = db.get_items_in_box(box["id"])
        lines = [f"Box #{box['number']}" + (f" ({box['label']})" if box["label"] else "")
                 + f" @ {box['location_name'] or 'location unknown'}:"]
        lines += [f"- {i['canonical_name']}" for i in items] or ["(empty)"]
        tg.send_message("\n".join(lines), thread_id)
        if box["photo_file_id"]:
            tg.send_photo(box["photo_file_id"], caption=f"Box #{box['number']} contents", thread_id=thread_id)
        return

    boxes = db.get_storage_boxes()
    stored = db.get_all_stored_items()
    if not boxes and not stored:
        tg.send_message("Nothing stored yet — /box to catalog your first box.", thread_id)
        return
    lines = ["Your stuff:"]
    for b in boxes:
        count = len(db.get_items_in_box(b["id"]))
        lines.append(f"Box #{b['number']}" + (f" ({b['label']})" if b["label"] else "")
                     + f" @ {b['location_name'] or 'location unknown'} — {count} item(s)")
    loose = [r for r in stored if r["box_number"] is None]
    for r in loose:
        lines.append(f"{r['canonical_name']} — loose @ {r['location_name'] or 'location unknown'}")
    lines.append("\n/stuff <box#> to see a box's contents.")
    tg.send_message("\n".join(lines), thread_id)


# ── command handling ─────────────────────────────────────────────────────

def handle_command(text: str, thread_id: int | None, msg: dict | None = None) -> None:
    parts = text.split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    args = parts[1:]
    msg = msg or {}

    if cmd == "start" and growth.needs_onboarding(msg):
        growth.handle_command("start", args, msg, thread_id)
    elif cmd == "help" and args and args[0].lower() == "growth":
        tg.send_message(growth.help_block(full=True), thread_id, parse_mode="HTML")
    elif cmd in ("help", "start"):
        tg.send_message(
            "<b>Semantic Task Manager</b>\n\n"
            "Mostly, just talk to me — no commands needed:\n"
            "• <i>\"caulk the bathroom sink\"</i> → saved as a task\n"
            "• a screenshot of a list, photo of a situation, or a recipe → tasks\n"
            "• <i>\"what can I do in 30 minutes?\"</i> → ranked, ready-to-go tasks\n"
            "• <i>\"I'm going to Home Depot\"</i> → your store list\n"
            "• <i>\"where are my snowshoes?\"</i> → box + location, with photos\n"
            "• <i>\"6 is done\"</i> → marks it done (with Undo)\n"
            "• reply to any \"Saved:\" message to correct it in plain English\n\n"
            "Putting stuff away: /box → photo of the contents → I catalog it, "
            "number the box, and remember where it lives. /loose for un-boxed items.\n\n"
            "Priorities via claude.ai: /export → paste the file into claude.ai → "
            "/priorities → paste its reply back. Then /next always knows your top task. "
            "Bulk import old tasks/Keep data: upload the scrape JSON, paste the prompt I "
            "return into claude.ai, then /import with its reply.\n\n"
            "The / menu lists every command (tap the / button). The precision "
            "versions still work: /tasks (sort: /tasks urgency · filter: /tasks kid stuff), "
            "/now, /window, /recurring, /find, /stuff, /store /online /groceries, "
            "/done /delete /category /dependency <code>id</code>, /unclassified, /sync, /cancel.\n\n"
            + growth.help_block(),
            thread_id, parse_mode="HTML",
        )
    elif cmd == "tasks":
        _handle_tasks_command(args, thread_id)
    elif cmd == "unclassified":
        rows = [r for r in db.get_all_tasks() if not r["classification_complete"]]
        if not rows:
            tg.send_message("Nothing unclassified 🎉", thread_id)
            return
        lines = ["<b>Unclassified tasks:</b>"] + [_task_line(r) for r in rows[:30]]
        if len(rows) > 30:
            lines.append(f"<i>…and {len(rows) - 30} more</i>")
        kb = {"inline_keyboard": [[{"text": "▶️ Classify now", "callback_data": "clsfstart"}]]}
        tg.send_message("\n".join(lines), thread_id, reply_markup=kb, parse_mode="HTML")
    elif cmd == "category":
        if len(args) < 2 or not args[0].isdigit():
            tg.send_message("Usage: /category <task_id> <name>", thread_id)
            return
        task_id, name = int(args[0]), " ".join(args[1:])
        db.update_task(task_id, category=name)
        tg.send_message(f"#{task_id} category set to \"{name}\".", thread_id)
    elif cmd == "delete":
        if not args or not args[0].isdigit():
            tg.send_message("Usage: /delete <task_id>", thread_id)
            return
        db.delete_task(int(args[0]))
        tg.send_message(f"Deleted #{args[0]}.", thread_id)
    elif cmd == "done":
        if not args or not args[0].isdigit():
            tg.send_message("Usage: /done <task_id>", thread_id)
            return
        _mark_done_with_undo(int(args[0]), thread_id)
    elif cmd == "dependency":
        _handle_dependency_command(args, thread_id)
    elif cmd == "now":
        if not args:
            growth.handle_command("now", [], msg, thread_id)
            return
        if not args[0].isdigit():
            tg.send_message("Usage: /now <minutes> — e.g. /now 30 (bare /now = refocus)", thread_id)
            return
        rows = context.eligible_tasks(int(args[0]))
        _send_task_list(rows, f"Eligible for {args[0]} minutes:", thread_id)
    elif cmd == "window":
        if not args:
            tg.send_message(
                "Usage: /window <description> — e.g. /window kid naps for 90 minutes, at home",
                thread_id,
            )
            return
        _handle_window_command(" ".join(args), thread_id)
    elif cmd == "recurring":
        _handle_recurring_command(args, thread_id)
    elif cmd in ("box", "loose"):
        storage_flows.start_ingest(cmd, thread_id)
    elif cmd == "find":
        _handle_find_command(args, thread_id)
    elif cmd == "stuff":
        _handle_stuff_command(args, thread_id)
    elif cmd == "cancel":
        note = storage_flows.cancel() or import_flows.cancel() \
            or growth.cancel((msg.get("from") or {}).get("id") or config.CHAT_ID)
        tg.send_message(note or "Nothing to cancel.", thread_id)
    elif cmd == "next":
        _show_next(0, thread_id)
    elif cmd == "export":
        prompt = ingest.build_priority_prompt()
        tg.send_document(prompt, "prioritize_tasks.txt", thread_id=thread_id,
                          caption="Paste this into claude.ai (Fable), then send /priorities "
                                  "here and paste its reply.")
    elif cmd == "import":
        import_flows.start_import(thread_id)
    elif cmd == "priorities":
        import_flows.start_priorities(thread_id)
    elif cmd in ("lock", "unlock"):
        if sync is None or not args or not args[0].isdigit():
            tg.send_message(f"Usage: /{cmd} <task_id> — forbid/allow other TaskBus apps "
                             "changing this task", thread_id)
            return
        if sync.set_lock(int(args[0]), cmd == "lock"):
            icon = "🔒" if cmd == "lock" else "🔓"
            tg.send_message(f"{icon} #{args[0]} {cmd}ed (applies on next sync).", thread_id)
        else:
            tg.send_message(f"#{args[0]} isn't synced to TaskBus yet — it locks on first sync.",
                             thread_id)
    elif cmd == "priority":
        if len(args) == 2 and args[0].isdigit() and args[1].isdigit():
            db.update_task(int(args[0]), priority=int(args[1]))
            tg.send_message(f"#{args[0]} priority set to {args[1]}.", thread_id)
        else:
            tg.send_message("Usage: /priority <task_id> <rank> (1 = highest)", thread_id)
    elif cmd == "sync":
        if sync is None:
            tg.send_message("Google Tasks sync isn't installed — see oauth_setup.py / requirements.txt.",
                             thread_id)
            return
        if not config.GOOGLE_TOKEN_PATH.exists():
            tg.send_message(
                "Google Tasks isn't authorized yet — run oauth_setup.py once, locally.", thread_id
            )
            return
        sync.run_sync()
        global _last_sync
        _last_sync = time.time()
        tg.send_message("Synced with Google Tasks.", thread_id)
    elif cmd in ("store", "online", "groceries"):
        list_name = {"store": db.SUPPLY_STORE_LIST, "online": db.ONLINE_LIST,
                     "groceries": db.GROCERY_LIST}[cmd]
        _send_shopping_list(list_name, thread_id)
    elif cmd in growth.COMMANDS:
        growth.handle_command(cmd, args, msg, thread_id)


def _notify_unblocked(finished_task_id: int, thread_id: int | None) -> None:
    for dep in db.get_dependents(finished_task_id):
        task_id = dep["task_id"]
        if not context.dependencies_met(task_id) or not context.requirements_satisfied(task_id):
            continue
        task = db.get_task(task_id)
        if task is None or task["status"] in ("done", "dropped"):
            continue
        tg.send_message(f"✅ Unblocked: #{task_id} {task['title']} is now ready.", thread_id)


def _check_recurring_windows() -> None:
    now = datetime.now(_TZ)
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    for window in db.get_recurring_windows():
        if window["day_of_week"] != now.weekday():
            continue
        if window["start_time"] != current_time:
            continue
        if window["last_notified_at"] == today:
            continue

        rows = context.eligible_tasks(window["duration_minutes"], window["location"])
        loc_suffix = f", at {window['location']}" if window["location"] else ""
        header = f"{DAY_NAMES[window['day_of_week']]} window ({window['duration_minutes']} min{loc_suffix}) — eligible:"
        _send_task_list(rows, header, None)
        db.mark_window_notified(window["id"], today)


# ── main loop ─────────────────────────────────────────────────────────────

def process_update(update: dict) -> None:
    global _offset
    _offset = max(_offset, update["update_id"] + 1)

    if "callback_query" in update:
        handle_callback_query(update["callback_query"])
        return

    msg = update.get("message")
    if not msg or msg.get("chat", {}).get("id") not in (config.CHAT_ID, growth.settings.CHAT_ID):
        return

    thread_id = msg.get("message_thread_id")
    text = msg.get("text", "").strip()
    reply_to = msg.get("reply_to_message")
    user_id = (msg.get("from") or {}).get("id") or config.CHAT_ID

    if text.startswith("/"):
        handle_command(text, thread_id, msg)
        return

    if msg.get("document"):
        _handle_document(msg, thread_id)
        return

    if import_flows.wants_text() and text:
        import_flows.handle_text(text, thread_id)
        return

    if reply_to:
        reply_id = reply_to["message_id"]
        if import_flows.wants_reply(reply_id):
            import_flows.handle_reply(text, thread_id)
            return
        if storage_flows.wants_reply(reply_id):
            storage_flows.handle_reply(text, thread_id)
            return
        if text and growth.wants_reply(user_id, reply_id):
            growth.handle_text(user_id, reply_id, text, msg)
            return
        cap = db.find_capture_by_awaiting_other(reply_id)
        if cap is not None:
            handle_other_reply(cap, text, thread_id)
            return
        cap = db.find_capture_by_bot_message(reply_id)
        if cap is not None:
            handle_correction(cap, text, thread_id)
            return

    if msg.get("photo"):
        if storage_flows.wants_photo():
            storage_flows.handle_photo(msg, thread_id)
        else:
            handle_photo_capture(msg, thread_id)
    elif text:
        if growth.claim_text(user_id, msg["chat"]["id"], thread_id, text, msg):
            return
        _route_text(text, msg["message_id"], thread_id)


def _sync_now(max_age: int = 120) -> bool:
    """Run the TaskBus sync if the last one is older than max_age seconds (growth hook)."""
    global _last_sync
    if sync is None or not config.GOOGLE_TOKEN_PATH.exists():
        return False
    if time.time() - _last_sync < max_age:
        return True
    sync.run_sync()
    _last_sync = time.time()
    return True


def main() -> None:
    global _offset, _last_sync, _last_window_check

    if not config.TELEGRAM_TOKEN:
        raise ValueError("STM_BOT_ID not set in .env")
    if not config.CHAT_ID:
        raise ValueError("STM_CHAT_ID not set in .env")
    if not config.ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not set in .env")

    db.init()
    growth.init()
    growth.hooks.sync_now = _sync_now
    tg.set_my_commands([
        ("next", "Your single top-priority task"),
        ("tasks", "Your tasks — tap one to manage it"),
        ("now", "What can I do right now? e.g. /now 30"),
        ("export", "Export tasks as a claude.ai prioritization prompt"),
        ("priorities", "Paste back claude.ai's prioritized list"),
        ("import", "Paste back claude.ai's grouped projects"),
        ("find", "Where is my …? e.g. /find snowgear"),
        ("stuff", "Browse boxes and storage locations"),
        ("box", "Put a box away: photo → cataloged"),
        ("loose", "Put un-boxed items away"),
        ("store", "Supply-store shopping list"),
        ("online", "Online shopping list"),
        ("groceries", "Grocery list"),
        ("window", "One-off free-time window, e.g. kid naps 90 min"),
        ("recurring", "Weekly window — I nudge you when it starts"),
        ("unclassified", "Tasks still waiting on classification"),
        ("sync", "Sync with Google Tasks now"),
        ("cancel", "Abandon the current flow"),
        ("help", "What can I send you?"),
    ] + growth.MENU_COMMANDS)
    print("Semantic task manager started (with growth).")

    while True:
        updates = tg.get_updates(_offset, timeout=growth.poll_timeout(30))
        for update in updates:
            try:
                process_update(update)
            except Exception as e:
                print(f"  error processing update: {e}")

        try:
            growth.tick()
        except Exception as e:
            print(f"  growth tick error: {e}")

        if sync is not None and config.GOOGLE_TOKEN_PATH.exists() \
                and time.time() - _last_sync > config.SYNC_INTERVAL_SECONDS \
                and not growth.busy():
            sync.run_sync()
            _last_sync = time.time()

        if time.time() - _last_window_check > config.WINDOW_CHECK_INTERVAL_SECONDS:
            try:
                _check_recurring_windows()
            except Exception as e:
                print(f"  recurring window check error: {e}")
            _last_window_check = time.time()

        if not updates:
            time.sleep(1)


def _cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args()
    main()


if __name__ == "__main__":
    _cli()
