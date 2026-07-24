"""Phase 1a interactive flows: classification, skill level + plan/supply
generation, entity normalization, inventory/shopping-list checklist, and the
recipe substitution flow. Triggered right after a task is created; callback
handling lives here, dispatched from bot.py by callback_data prefix."""
from __future__ import annotations

import json

import ai
import db
import telegram_api as tg

DIMS = ["urgency", "interest", "energy", "value"]
DIM_ABBR = {"urgency": "u", "interest": "i", "energy": "e", "value": "v"}
ABBR_DIM = {v: k for k, v in DIM_ABBR.items()}


# ── classification (urgency / interest / energy / value) ─────────────────

def _classification_text(task_id: int) -> str:
    row = db.get_task(task_id)
    return f"Classify #{task_id} {row['title']}:"


def _classification_keyboard(task_id: int) -> dict:
    row = db.get_task(task_id)
    rows = []
    for dim in DIMS:
        abbr = DIM_ABBR[dim]
        val = row[dim] or 0
        rows.append([
            {"text": "-", "callback_data": f"clsf:{task_id}:{abbr}:-1"},
            {"text": f"{dim.capitalize()}: {val}", "callback_data": "noop"},
            {"text": "+", "callback_data": f"clsf:{task_id}:{abbr}:1"},
        ])
    rows.append([{"text": "✓ Looks good", "callback_data": f"clsf:{task_id}:go:0"}])
    return {"inline_keyboard": rows}


def start_classification(task_id: int, thread_id: int | None) -> None:
    row = db.get_task(task_id)
    try:
        guess = ai.classify_task(row["title"], row["description"])
        db.update_task(task_id, **guess)
    except Exception as e:
        print(f"  classify error: {e}")
    tg.send_message(_classification_text(task_id), thread_id, reply_markup=_classification_keyboard(task_id))


def handle_classification_callback(cq: dict, task_id: int, dim_abbr: str, delta: str) -> None:
    message_id = cq["message"]["message_id"]
    thread_id = cq["message"].get("message_thread_id")

    if dim_abbr == "go":
        db.update_task(task_id, classification_complete=1)
        tg.edit_message(message_id, f"Classified #{task_id} ✓")
        tg.answer_callback_query(cq["id"])
        _advance_after_classification(task_id, thread_id)
        return

    dim = ABBR_DIM[dim_abbr]
    row = db.get_task(task_id)
    new_val = max(0, min(5, (row[dim] or 0) + int(delta)))
    db.update_task(task_id, **{dim: new_val})
    tg.edit_message(message_id, _classification_text(task_id), reply_markup=_classification_keyboard(task_id))
    tg.answer_callback_query(cq["id"])


def _advance_after_classification(task_id: int, thread_id: int | None) -> None:
    proc = db.get_procedure_by_task(task_id)
    if proc is None:
        start_skill_prompt(task_id, thread_id)
    elif proc["type"] == "recipe":
        start_recipe_requirements(task_id, thread_id, proc)
    elif proc["type"] == "instructions":
        start_instruction_requirements(task_id, thread_id, proc)
    else:
        start_skill_prompt(task_id, thread_id)


# ── skill level + plan/supply generation (generic actionable tasks) ─────

def start_skill_prompt(task_id: int, thread_id: int | None) -> None:
    row = db.get_task(task_id)
    keyboard = {"inline_keyboard": [
        [{"text": str(i), "callback_data": f"skill:{task_id}:{i}"} for i in range(6)]
    ]}
    tg.send_message(
        f"Skill level needed for #{task_id} {row['title']}? (0=novice, needs full instructions — 5=expert)",
        thread_id, reply_markup=keyboard,
    )


def handle_skill_callback(cq: dict, task_id: int, level: str) -> None:
    message_id = cq["message"]["message_id"]
    thread_id = cq["message"].get("message_thread_id")
    db.update_task(task_id, skill_level_required=int(level))
    tg.edit_message(message_id, f"Skill level set: {level}")
    tg.answer_callback_query(cq["id"])
    _generate_plan(task_id, thread_id)


def _generate_plan(task_id: int, thread_id: int | None) -> None:
    row = db.get_task(task_id)
    try:
        plan = ai.generate_plan(row["title"], row["description"], row["skill_level_required"] or 0)
    except Exception as e:
        tg.send_message(f"Couldn't generate a plan: {e}", thread_id)
        return

    db.update_task(
        task_id,
        estimated_active_minutes=plan.get("estimated_active_minutes"),
        estimated_passive_minutes=plan.get("estimated_passive_minutes"),
    )
    steps = plan.get("plan", [])
    if steps:
        tg.send_message("Plan:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)), thread_id)

    proc_id = db.create_procedure(task_id, "plan", None, {"supply_matches": []})
    _normalize_and_store(task_id, thread_id, proc_id, plan.get("supplies", []))


# ── entity normalization (shared by plan / recipe / instructions flows) ─

def _normalize_and_store(task_id: int, thread_id: int | None, proc_id: int, names: list[str]) -> None:
    existing = db.get_entities_by_type("supply")
    try:
        matches = ai.normalize_entities(names, "supply", existing) if names else []
    except Exception as e:
        tg.send_message(f"Couldn't process supplies: {e}", thread_id)
        matches = []

    proc = db.get_procedure(proc_id)
    content = json.loads(proc["content_json"])
    content["supply_matches"] = matches
    db.update_procedure_content(proc_id, content)

    pending_new = [m for m in matches if not m.get("entity_id")]
    if pending_new:
        names_str = ", ".join(m["new_canonical_name"] for m in pending_new)
        kb = {"inline_keyboard": [[{"text": "Add these supplies", "callback_data": f"newent:{task_id}:{proc_id}"}]]}
        tg.send_message(f"New supplies to add: {names_str}", thread_id, reply_markup=kb)
    else:
        _finalize_checklist(task_id, thread_id, proc_id)


def handle_newent_callback(cq: dict, task_id: int, proc_id: int) -> None:
    proc = db.get_procedure(proc_id)
    content = json.loads(proc["content_json"])
    matches = content.get("supply_matches", [])
    for m in matches:
        if not m.get("entity_id"):
            m["entity_id"] = db.create_entity("supply", m["new_canonical_name"])
    db.update_procedure_content(proc_id, content)

    tg.edit_message(cq["message"]["message_id"], "Added ✓")
    tg.answer_callback_query(cq["id"])
    thread_id = cq["message"].get("message_thread_id")

    if proc["type"] == "recipe":
        _finalize_recipe(task_id, thread_id, proc_id)
    else:
        _finalize_checklist(task_id, thread_id, proc_id)


# ── generic supply checklist (plan / instructions flows) ────────────────

def _checklist_view(task_id: int) -> tuple[str, dict]:
    reqs = db.get_requirements_for_task(task_id)
    rows = [
        [{"text": f"{'✅ Own it' if r['satisfied'] else '🛒 Need it'} — {r['canonical_name']}",
          "callback_data": f"chk:{task_id}:{r['entity_id']}"}]
        for r in reqs
    ]
    rows.append([{"text": "Done", "callback_data": f"chkdone:{task_id}"}])
    text = (f"Supply checklist for #{task_id} — tap an item to toggle whether you already own it.\n"
            "🛒 items get added to your shopping lists; ✅ items get removed.")
    return text, {"inline_keyboard": rows}


def _finalize_checklist(task_id: int, thread_id: int | None, proc_id: int) -> None:
    proc = db.get_procedure(proc_id)
    content = json.loads(proc["content_json"])
    for m in content.get("supply_matches", []):
        eid = m["entity_id"]
        inv = db.get_inventory(eid)
        owned = bool(inv["on_hand"]) if inv else False
        db.create_requirement(task_id, eid, "supply", satisfied=owned)
        if not owned:
            db.add_shopping_item(db.SUPPLY_STORE_LIST, eid, task_id)
            db.add_shopping_item(db.ONLINE_LIST, eid, task_id)

    if not content.get("supply_matches"):
        tg.send_message(f"No supplies needed for #{task_id}.", thread_id)
        return
    text, kb = _checklist_view(task_id)
    tg.send_message(text, thread_id, reply_markup=kb)


def handle_checklist_toggle(cq: dict, task_id: int, entity_id: int) -> None:
    req = db.get_requirement(task_id, entity_id)
    if req is None:
        tg.answer_callback_query(cq["id"], "Not found.")
        return
    new_satisfied = not bool(req["satisfied"])
    db.set_requirement_satisfied(task_id, entity_id, new_satisfied)
    db.set_inventory(entity_id, new_satisfied)
    if new_satisfied:
        db.remove_shopping_item(db.SUPPLY_STORE_LIST, entity_id, task_id)
        db.remove_shopping_item(db.ONLINE_LIST, entity_id, task_id)
    else:
        db.add_shopping_item(db.SUPPLY_STORE_LIST, entity_id, task_id)
        db.add_shopping_item(db.ONLINE_LIST, entity_id, task_id)
    text, kb = _checklist_view(task_id)
    tg.edit_message(cq["message"]["message_id"], text, reply_markup=kb)
    tg.answer_callback_query(cq["id"])


def handle_checklist_done(cq: dict, task_id: int) -> None:
    reqs = db.get_requirements_for_task(task_id)
    owned = [r["canonical_name"] for r in reqs if r["satisfied"]]
    needed = [r["canonical_name"] for r in reqs if not r["satisfied"]]
    lines = [f"Checklist saved for #{task_id}."]
    if owned:
        lines.append("Own: " + ", ".join(owned))
    if needed:
        lines.append("On shopping lists: " + ", ".join(needed) + " (see /store or /online)")
    tg.edit_message(cq["message"]["message_id"], "\n".join(lines))
    tg.answer_callback_query(cq["id"])


# ── instruction-type procedures (lighter treatment, no plan generation) ─

def start_instruction_requirements(task_id: int, thread_id: int | None, proc) -> None:
    content = json.loads(proc["content_json"])
    steps = content.get("steps", [])
    try:
        names = ai.extract_supplies(steps) if steps else []
    except Exception as e:
        tg.send_message(f"Couldn't extract supplies: {e}", thread_id)
        names = []
    _normalize_and_store(task_id, thread_id, proc["id"], names)


# ── recipe flow (ingredient checklist + substitution proposals) ─────────

def start_recipe_requirements(task_id: int, thread_id: int | None, proc) -> None:
    content = json.loads(proc["content_json"])
    names = [i["name"] for i in content.get("ingredients", [])]
    _normalize_and_store(task_id, thread_id, proc["id"], names)


def _finalize_recipe(task_id: int, thread_id: int | None, proc_id: int) -> None:
    proc = db.get_procedure(proc_id)
    content = json.loads(proc["content_json"])
    matches = content.get("supply_matches", [])
    recipe_context = {
        "title": proc["title"],
        "ingredients": content.get("ingredients", []),
        "steps": content.get("steps", []),
    }

    owned_names = []
    missing = []
    for m in matches:
        eid = m["entity_id"]
        inv = db.get_inventory(eid)
        owned = bool(inv["on_hand"]) if inv else False
        if owned:
            db.create_requirement(task_id, eid, "supply", satisfied=True)
            owned_names.append(m["name"])
        else:
            missing.append(m)

    lines = [f"Ingredient checklist for #{task_id} {proc['title']}:"]
    lines += [f"☑ {n}" for n in owned_names]
    lines += [f"☐ {m['name']} (see below)" for m in missing]
    tg.send_message("\n".join(lines), thread_id)

    inventory_names = _owned_supply_names()
    for m in missing:
        eid = m["entity_id"]
        try:
            sub = ai.propose_substitution(m["name"], recipe_context, inventory_names)
        except Exception:
            sub = ""
        db.create_requirement(task_id, eid, "supply", satisfied=False, note=sub or None)
        kb = {"inline_keyboard": [[
            {"text": "Use substitution", "callback_data": f"sub:{task_id}:{eid}:use"},
            {"text": "Buy", "callback_data": f"sub:{task_id}:{eid}:buy"},
        ]]}
        text = f"Missing: {m['name']}\nSubstitute: {sub}" if sub else f"Missing: {m['name']}"
        tg.send_message(text, thread_id, reply_markup=kb)


def _owned_supply_names() -> list[str]:
    supplies = db.get_entities_by_type("supply")
    names = []
    for s in supplies:
        inv = db.get_inventory(s["id"])
        if inv and inv["on_hand"]:
            names.append(s["canonical_name"])
    return names


def handle_substitution_callback(cq: dict, task_id: int, entity_id: int, action: str) -> None:
    req = db.get_requirement(task_id, entity_id)
    entity = db.get_entity(entity_id)
    if action == "use":
        db.set_requirement_satisfied(task_id, entity_id, True)
        note = req["note"] if req else ""
        tg.edit_message(cq["message"]["message_id"], f"Using substitution for {entity['canonical_name']}: {note}")
    else:
        db.add_shopping_item(db.GROCERY_LIST, entity_id, task_id)
        tg.edit_message(cq["message"]["message_id"], f"Added {entity['canonical_name']} to groceries.")
    tg.answer_callback_query(cq["id"])
