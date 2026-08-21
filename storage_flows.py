"""Storage tracker ingest flow: /box (and /loose) guided state machine.

Photo-first UX: the user photographs box contents and storage locations; the
AI names everything (items + topic tags via Sonnet vision, location names
from a photo). Single-user bot, so one module-level pending state is enough.
Callback prefix: "stor". bot.py routes photos/replies here first while a
flow is active."""
from __future__ import annotations

import ai
import db
import telegram_api as tg

_state: dict | None = None


def active() -> bool:
    return _state is not None


def cancel() -> str | None:
    global _state
    if _state is None:
        return None
    _state = None
    return "Storage flow cancelled."


def wants_photo() -> bool:
    return _state is not None and _state["step"] in ("await_contents_photo", "await_location_photo")


def wants_reply(reply_to_message_id: int) -> bool:
    if _state is None:
        return False
    if _state["step"] == "confirm_items":
        return reply_to_message_id == _state.get("confirm_message_id")
    if _state["step"] == "confirm_location":
        return reply_to_message_id == _state.get("location_confirm_message_id")
    return False


# ── flow start ───────────────────────────────────────────────────────────

def start_ingest(mode: str, thread_id: int | None) -> None:
    """mode: 'box' (items live in a numbered box) or 'loose' (items live
    directly at a location)."""
    global _state
    _state = {"step": "await_contents_photo", "mode": mode, "thread_id": thread_id}
    prompt = ("Send a photo of the box contents (or one item)."
              if mode == "box" else "Send a photo of the item(s).")
    tg.send_message(f"{prompt}\n(/cancel to stop)", thread_id)


# ── photos ───────────────────────────────────────────────────────────────

def handle_photo(msg: dict, thread_id: int | None) -> None:
    file_id = msg["photo"][-1]["file_id"]
    image_bytes = tg.download_file(file_id)
    if image_bytes is None:
        tg.send_message("Couldn't download that photo — try again.", thread_id)
        return

    if _state["step"] == "await_contents_photo":
        _handle_contents_photo(file_id, image_bytes, thread_id)
    elif _state["step"] == "await_location_photo":
        _handle_location_photo(file_id, image_bytes, thread_id)


def _handle_contents_photo(file_id: str, image_bytes: bytes, thread_id: int | None) -> None:
    try:
        items = ai.identify_items_in_photo(image_bytes)
    except Exception as e:
        tg.send_message(f"Couldn't read that photo: {e}", thread_id)
        return
    if not items:
        tg.send_message("Couldn't identify any items — try a clearer photo, or /cancel.", thread_id)
        return
    _state["items"] = items
    _state["contents_file_id"] = file_id
    _send_items_confirm(thread_id)


def _send_items_confirm(thread_id: int | None) -> None:
    lines = ["I see:"]
    for i, it in enumerate(_state["items"], 1):
        lines.append(f"{i}. {it['name']}")
    lines.append("\nReply to this message to correct the list, or confirm:")
    kb = {"inline_keyboard": [[
        {"text": "✓ Looks right", "callback_data": "stor:items_ok"},
        {"text": "Cancel", "callback_data": "stor:cancel"},
    ]]}
    msg_id = tg.send_message("\n".join(lines), thread_id, reply_markup=kb)
    _state["confirm_message_id"] = msg_id
    _state["step"] = "confirm_items"


def _handle_location_photo(file_id: str, image_bytes: bytes, thread_id: int | None) -> None:
    try:
        name = ai.name_location_from_photo(image_bytes)
    except Exception as e:
        tg.send_message(f"Couldn't read that photo: {e}", thread_id)
        return
    if not name:
        name = "unnamed location"
    _state["location_file_id"] = file_id
    _state["pending_location_name"] = name
    _send_location_confirm(thread_id)


def _send_location_confirm(thread_id: int | None) -> None:
    kb = {"inline_keyboard": [[
        {"text": "✓ Use this name", "callback_data": "stor:loc_ok"},
        {"text": "Cancel", "callback_data": "stor:cancel"},
    ]]}
    msg_id = tg.send_message(
        f"Location: \"{_state['pending_location_name']}\"\n"
        "Reply to this message with a different name, or confirm:",
        thread_id, reply_markup=kb,
    )
    _state["location_confirm_message_id"] = msg_id
    _state["step"] = "confirm_location"


# ── correction replies ───────────────────────────────────────────────────

def handle_reply(text: str, thread_id: int | None) -> None:
    if _state["step"] == "confirm_items":
        try:
            corrected = ai.correct_item_list(_state["items"], text)
        except Exception as e:
            tg.send_message(f"Couldn't apply that correction: {e}", thread_id)
            return
        if corrected:
            _state["items"] = corrected
        _send_items_confirm(thread_id)
    elif _state["step"] == "confirm_location":
        # typing the name IS the correction — no AI needed
        _state["pending_location_name"] = text.strip()
        _send_location_confirm(thread_id)


# ── callbacks (prefix "stor") ────────────────────────────────────────────

def handle_callback(cq: dict, parts: list[str]) -> None:
    global _state
    action = parts[1]
    thread_id = cq["message"].get("message_thread_id")

    if action == "cancel":
        _state = None
        tg.edit_message(cq["message"]["message_id"], "Storage flow cancelled.")
        tg.answer_callback_query(cq["id"])
        return

    if _state is None:
        tg.answer_callback_query(cq["id"], "This flow has expired — start again with /box.")
        return

    if action == "items_ok":
        tg.edit_message(cq["message"]["message_id"],
                         "Items confirmed:\n" + "\n".join(f"- {it['name']}" for it in _state["items"]))
        tg.answer_callback_query(cq["id"])
        if _state["mode"] == "box":
            _ask_box(thread_id)
        else:
            _ask_location(thread_id)
        return

    if action == "box":
        _choose_box(cq, parts[2], thread_id)
        return

    if action == "loc":
        _choose_location(cq, parts[2], thread_id)
        return

    if action == "loc_ok":
        location_id = db.create_storage_location(
            _state["pending_location_name"], _state.get("location_file_id")
        )
        tg.edit_message(cq["message"]["message_id"],
                         f"Location saved: {_state['pending_location_name']}")
        tg.answer_callback_query(cq["id"])
        _apply_location(location_id, thread_id)
        return


def _ask_box(thread_id: int | None) -> None:
    rows = [[{"text": "New box", "callback_data": "stor:box:new"}]]
    for b in db.get_storage_boxes():
        label = f"#{b['number']}" + (f" {b['label']}" if b["label"] else "")
        if b["location_name"]:
            label += f" @ {b['location_name']}"
        rows.append([{"text": label, "callback_data": f"stor:box:{b['id']}"}])
    tg.send_message("Which box?", thread_id, reply_markup={"inline_keyboard": rows})
    _state["step"] = "choose_box"


def _choose_box(cq: dict, choice: str, thread_id: int | None) -> None:
    if choice == "new":
        box_id = db.create_storage_box(_state.get("contents_file_id"), None)
        _state["box_id"] = box_id
        box = db.get_box(box_id)
        tg.edit_message(cq["message"]["message_id"],
                         f"New box created: #{box['number']} (write that on the box).")
        tg.answer_callback_query(cq["id"])
        _ask_location(thread_id)
        return

    box_id = int(choice)
    box = db.get_box(box_id)
    if box is None:
        tg.answer_callback_query(cq["id"], "Box not found.")
        return
    _state["box_id"] = box_id
    tg.edit_message(cq["message"]["message_id"], f"Box: #{box['number']}")
    tg.answer_callback_query(cq["id"])
    if box["location_id"]:
        _finalize(thread_id)
    else:
        _ask_location(thread_id)


def _ask_location(thread_id: int | None) -> None:
    rows = [[{"text": "New location — send a photo", "callback_data": "stor:loc:new"}]]
    for loc in db.get_storage_locations():
        rows.append([{"text": loc["name"], "callback_data": f"stor:loc:{loc['id']}"}])
    prompt = "Where is this box?" if _state["mode"] == "box" else "Where do these live?"
    tg.send_message(prompt, thread_id, reply_markup={"inline_keyboard": rows})
    _state["step"] = "choose_location"


def _choose_location(cq: dict, choice: str, thread_id: int | None) -> None:
    if choice == "new":
        tg.edit_message(cq["message"]["message_id"], "Send a photo of the location.")
        tg.answer_callback_query(cq["id"])
        _state["step"] = "await_location_photo"
        return

    location_id = int(choice)
    loc = db.get_storage_location(location_id)
    if loc is None:
        tg.answer_callback_query(cq["id"], "Location not found.")
        return
    tg.edit_message(cq["message"]["message_id"], f"Location: {loc['name']}")
    tg.answer_callback_query(cq["id"])
    _apply_location(location_id, thread_id)


def _apply_location(location_id: int, thread_id: int | None) -> None:
    if _state["mode"] == "box":
        db.set_box_location(_state["box_id"], location_id)
    else:
        _state["location_id"] = location_id
    _finalize(thread_id)


# ── save ─────────────────────────────────────────────────────────────────

def _finalize(thread_id: int | None) -> None:
    global _state
    items = _state["items"]
    box_id = _state.get("box_id")
    location_id = _state.get("location_id")

    names = [it["name"] for it in items]
    try:
        matches = ai.normalize_entities(names, "supply", db.get_entities_by_type("supply"))
    except Exception as e:
        tg.send_message(f"Couldn't save items: {e}", thread_id)
        return
    by_name = {m["name"]: m for m in matches}

    stored = []
    for it in items:
        m = by_name.get(it["name"])
        entity_id = (m or {}).get("entity_id")
        if not entity_id:
            canonical = (m or {}).get("new_canonical_name") or it["name"].lower()
            entity_id = db.create_entity("supply", canonical)
        db.store_item(entity_id, box_id, location_id)
        db.set_inventory(entity_id, True)
        db.add_entity_alias(entity_id, it["name"])
        for topic in it.get("topics", []):
            db.add_entity_alias(entity_id, topic)
        stored.append(db.get_entity(entity_id)["canonical_name"])

    if box_id:
        box = db.get_box(box_id)
        where = f"Box #{box['number']} @ {box['location_name'] or '?'}"
    else:
        loc = db.get_storage_location(location_id) if location_id else None
        where = loc["name"] if loc else "?"

    tg.send_message(
        f"Stored {len(stored)} item(s) — {where}:\n" + "\n".join(f"- {n}" for n in stored)
        + "\n\nFind them later with /find <what you're looking for>.",
        thread_id,
    )
    _state = None
