"""Anthropic calls for Phase 0 capture and correction.

Model tiering per spec: Haiku for high-volume/low-stakes text parsing and
correction; Sonnet for vision (list/situation/procedure classification)."""
from __future__ import annotations

import json
import re

import anthropic

import config

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _extract_json(text: str) -> dict:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


def _call(model: str, system: str, content: list[dict], max_tokens: int = 1024) -> dict:
    resp = _client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
    )
    raw = "".join(block.text for block in resp.content if block.type == "text")
    return _extract_json(raw)


# ── text capture ─────────────────────────────────────────────────────────

TEXT_SYSTEM = """You split an inbound message into one or more discrete tasks.
Respond with strict JSON only, no prose, no markdown fences:
{"tasks": [{"title": "short imperative title", "description": "optional extra detail or null"}]}
If the message is clearly a single task, return one item. If it lists several distinct
things to do, split them. Keep titles short and actionable."""


def parse_text_to_tasks(text: str) -> list[dict]:
    result = _call(config.MODEL_HAIKU, TEXT_SYSTEM, [{"type": "text", "text": text}])
    return result.get("tasks", [])


# ── image capture ────────────────────────────────────────────────────────

IMAGE_SYSTEM = """You classify an inbound photo/screenshot for a task-capture system
into exactly one of three kinds, and extract accordingly. Respond with strict JSON only,
no prose, no markdown fences.

- "list": a screenshot of a list of things to do (e.g. a checklist, notes app, text list).
  Transcribe each line into a task: {"kind": "list", "tasks": [{"title": "...", "description": null}]}
- "situation": a photo of a real-world thing/situation implying possible tasks (e.g. a messy
  garage, a broken faucet, a car dent). Propose 3-4 short candidate task titles:
  {"kind": "situation", "candidates": ["...", "...", "...", "..."]}
- "procedure": an image of a recipe or instructions (e.g. a recipe card, a manual page).
  Parse it into structured steps: {"kind": "procedure", "procedure_type": "recipe" or "instructions",
  "title": "...", "task_title": "short task title like 'Cook <dish>' or 'Install <thing>'",
  "ingredients": [{"name": "...", "quantity": "..."}] (recipes only, else []),
  "steps": [{"text": "...", "active_minutes": number or null, "passive_minutes": number or null}]}
"""


def classify_photo(image_bytes: bytes, media_type: str) -> dict:
    import base64
    b64 = base64.b64encode(image_bytes).decode("ascii")
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
        {"type": "text", "text": "Classify and extract per the system instructions."},
    ]
    return _call(config.MODEL_SONNET, IMAGE_SYSTEM, content, max_tokens=2048)


# ── correction loop ──────────────────────────────────────────────────────

CORRECTION_SYSTEM = """You apply a natural-language correction instruction to a set of
just-captured tasks. You are given the current tasks as JSON (id, title, description) and
a free-text instruction (e.g. "second one is 'call dentist', delete the third, merge 1 and 4").
Respond with strict JSON only, no prose, no markdown fences:
{"edit": [{"id": 1, "title": "...", "description": "..." or null}],
 "delete": [3],
 "merge": [{"ids": [1, 4], "title": "...", "description": "..." or null}],
 "add": [{"title": "...", "description": "..." or null}]}
Omit keys with nothing to do (use empty lists). Reference tasks by their position in the
list the user was shown (1-indexed) — map back to the given ids yourself."""


def apply_correction(tasks: list[dict], instruction: str) -> dict:
    payload = {"tasks": tasks, "instruction": instruction}
    content = [{"type": "text", "text": json.dumps(payload)}]
    return _call(config.MODEL_HAIKU, CORRECTION_SYSTEM, content)


# ── Phase 1a: classification ─────────────────────────────────────────────

CLASSIFY_SYSTEM = """You estimate four 0-5 ratings for a task, guessing from its title/description
alone. Respond with strict JSON only, no prose, no markdown fences:
{"urgency": 0-5, "interest": 0-5, "energy": 0-5, "value": 0-5}
urgency: how time-sensitive. interest: how enjoyable/engaging to the person doing it.
energy: how much physical/mental energy it demands. value: how much it matters long-term."""


def classify_task(title: str, description: str | None) -> dict:
    text = title + (f"\n{description}" if description else "")
    result = _call(config.MODEL_HAIKU, CLASSIFY_SYSTEM, [{"type": "text", "text": text}], max_tokens=256)
    return {k: max(0, min(5, int(result.get(k, 0)))) for k in ("urgency", "interest", "energy", "value")}


# ── Phase 1a+: category / life-aspect tagging ────────────────────────────

CATEGORY_SYSTEM = """You tag a task with a short life-aspect category (e.g. "Kid stuff", "Wife",
"Career", "Home", "Health", "Finances"). You are given the task and the person's existing
categories — reuse one if it clearly fits, otherwise propose a short new one (2-3 words, title
case). Respond with strict JSON only, no prose, no markdown fences: {"category": "..."}"""


def guess_category(title: str, description: str | None, existing_categories: list[str]) -> str:
    payload = {"task": title + (f"\n{description}" if description else ""),
               "existing_categories": existing_categories}
    result = _call(config.MODEL_HAIKU, CATEGORY_SYSTEM, [{"type": "text", "text": json.dumps(payload)}],
                    max_tokens=64)
    return result.get("category", "").strip()


# ── Phase 1b: availability window parsing ────────────────────────────────

WINDOW_SYSTEM = """You parse a free-text description of an availability window into structured
data for a task-matching engine. Respond with strict JSON only, no prose, no markdown fences:
{"duration_minutes": number or null, "location": "..." or null, "notes": "..." or null}
duration_minutes is how much active time the person actually has (e.g. "kid naps for 90 minutes"
-> 90; "Sunday 9am-1pm" -> 240). location is a short place name if stated (e.g. "at home",
"garage"), else null. notes captures any other constraint verbatim (e.g. "kid must stay asleep"),
else null. If no duration can be determined, set duration_minutes to null."""


def parse_window(text: str) -> dict:
    return _call(config.MODEL_HAIKU, WINDOW_SYSTEM, [{"type": "text", "text": text}], max_tokens=256)


RECURRING_WINDOW_SYSTEM = """You parse a free-text description of a RECURRING weekly availability
window into structured data. Respond with strict JSON only, no prose, no markdown fences:
{"day_of_week": 0-6 or null, "start_time": "HH:MM" or null, "duration_minutes": number or null,
"location": "..." or null, "notes": "..." or null}
day_of_week uses Monday=0 .. Sunday=6. start_time is 24-hour "HH:MM" for when the window begins
(e.g. "every Sunday 9am for 4 hours" -> day_of_week 6, start_time "09:00", duration_minutes 240).
If the text implies multiple days (e.g. "weekday mornings"), pick the single most representative
day and note the rest in "notes" — this system only supports one day per recurring window.
If day_of_week, start_time, or duration_minutes can't be determined, set it to null."""


def parse_recurring_window(text: str) -> dict:
    return _call(config.MODEL_HAIKU, RECURRING_WINDOW_SYSTEM, [{"type": "text", "text": text}], max_tokens=256)


# ── Phase 1a: plan + supply list ─────────────────────────────────────────

PLAN_SYSTEM = """You produce a short step-by-step plan and a tool/supply list for a task,
calibrated to the stated skill level (0 = amateur, needs full instructions with no assumed
knowledge; 5 = expert, needs only a checklist). Respond with strict JSON only, no prose, no
markdown fences:
{"plan": ["step 1", "step 2", ...], "supplies": ["supply or tool name", ...],
 "estimated_active_minutes": number, "estimated_passive_minutes": number}
estimated_passive_minutes is unattended time (e.g. glue drying) that blocks the area but
needs no active work; 0 if none."""


def generate_plan(title: str, description: str | None, skill_level: int) -> dict:
    text = json.dumps({"title": title, "description": description, "skill_level": skill_level})
    return _call(config.MODEL_SONNET, PLAN_SYSTEM, [{"type": "text", "text": text}], max_tokens=1536)


# ── Phase 1a: entity normalization ───────────────────────────────────────

NORMALIZE_SYSTEM = """You normalize free-text item names against a canonical entity list for a
personal task-management system. You are given a list of candidate names and the existing
canonical entities of the relevant type (id, canonical_name, aliases). For each candidate,
either match it to an existing entity id (same real-world item, allowing synonyms/typos/case),
or propose it as a new entity if nothing matches. Respond with strict JSON only, no prose, no
markdown fences:
{"matches": [{"name": "candidate as given", "entity_id": 1 or null, "new_canonical_name": "..." or null}]}
Exactly one of entity_id/new_canonical_name must be non-null per item. Use a clean, singular,
lowercase canonical_name for new entities (e.g. "caulk gun", not "Caulk Guns")."""


def normalize_entities(names: list[str], entity_type: str, existing: list[dict]) -> list[dict]:
    if not names:
        return []
    payload = {"type": entity_type, "candidates": names, "existing_entities": existing}
    result = _call(config.MODEL_HAIKU, NORMALIZE_SYSTEM, [{"type": "text", "text": json.dumps(payload)}],
                    max_tokens=1536)
    return result.get("matches", [])


# ── Phase 1a: supply extraction for instruction-type procedures ─────────

EXTRACT_SUPPLIES_SYSTEM = """You read a list of instruction steps (e.g. for a router install or
filter change) and list the distinct tools/supplies/parts they require. Respond with strict JSON
only, no prose, no markdown fences: {"supplies": ["tool or supply name", ...]}"""


def extract_supplies(steps: list[dict]) -> list[str]:
    text = "\n".join(s.get("text", "") for s in steps)
    result = _call(config.MODEL_HAIKU, EXTRACT_SUPPLIES_SYSTEM, [{"type": "text", "text": text}], max_tokens=512)
    return result.get("supplies", [])


# ── Phase 1a: recipe ingredient substitution ─────────────────────────────

SUBSTITUTION_SYSTEM = """You propose exactly one substitution for a missing recipe ingredient.
You are given the full recipe (for culinary context) and the person's current inventory (so you
prefer a substitute they already have on hand). Respond with strict JSON only, no prose, no
markdown fences: {"substitute": "short description of the substitution"}"""


def propose_substitution(missing_ingredient: str, recipe: dict, inventory_names: list[str]) -> str:
    payload = {"missing_ingredient": missing_ingredient, "recipe": recipe, "on_hand": inventory_names}
    result = _call(config.MODEL_HAIKU, SUBSTITUTION_SYSTEM, [{"type": "text", "text": json.dumps(payload)}],
                    max_tokens=256)
    return result.get("substitute", "")


# ── storage tracker: vision ingest + search matching ─────────────────────

def _image_content(image_bytes: bytes, prompt: str, media_type: str = "image/jpeg") -> list[dict]:
    import base64
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return [
        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
        {"type": "text", "text": prompt},
    ]


IDENTIFY_ITEMS_SYSTEM = """You catalog the contents of a storage box (or a single item) from a
photo, for a personal where-is-my-stuff tracker. List every distinct, individually-storable
item you can identify — not packaging, background objects, the box itself, or the surface it
sits on. Use short specific names, including brand when clearly visible (e.g. "Fanttik electric
screwdriver", not "tool"). For each item add 2-4 short lowercase topic tags a person might
search by later (e.g. snowshoes -> ["snowgear", "winter", "hiking"]). Respond with strict JSON
only, no prose, no markdown fences:
{"items": [{"name": "...", "topics": ["...", "..."]}]}"""


def identify_items_in_photo(image_bytes: bytes, media_type: str = "image/jpeg") -> list[dict]:
    result = _call(config.MODEL_SONNET, IDENTIFY_ITEMS_SYSTEM,
                    _image_content(image_bytes, "Catalog the items in this photo.", media_type),
                    max_tokens=1536)
    return result.get("items", [])


NAME_LOCATION_SYSTEM = """You name a storage location from a photo, for a personal
where-is-my-stuff tracker. Produce a short, distinctive place name a person would recognize
("white shelves in bedroom", "garage wall pegboard", "hall closet top shelf"). Respond with
strict JSON only, no prose, no markdown fences: {"name": "..."}"""


def name_location_from_photo(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    result = _call(config.MODEL_SONNET, NAME_LOCATION_SYSTEM,
                    _image_content(image_bytes, "Name this storage location.", media_type),
                    max_tokens=128)
    return result.get("name", "").strip()


MATCH_ITEMS_SYSTEM = """You match a free-text search query against a person's stored-item list.
You are given the query and the items (entity_id, name, aliases). Return every item that
plausibly matches the query — by name, synonym, category, or purpose (e.g. query "snowgear"
matches snowshoes; "screwdrivers" matches an electric screwdriver set). Respond with strict
JSON only, no prose, no markdown fences: {"entity_ids": [1, 2]}
Return an empty list if nothing matches. Do not invent ids."""


def match_stored_items(query: str, items: list[dict]) -> list[int]:
    payload = {"query": query, "items": items}
    result = _call(config.MODEL_HAIKU, MATCH_ITEMS_SYSTEM,
                    [{"type": "text", "text": json.dumps(payload)}], max_tokens=512)
    return [i for i in result.get("entity_ids", []) if isinstance(i, int)]


ITEM_LIST_CORRECTION_SYSTEM = """You apply a natural-language correction to a list of items
identified in a storage-box photo, before they are saved. You are given the current items
(1-indexed position, name, topics) and an instruction (e.g. "the second one is a Makita drill,
delete the last one, add ski gloves"). Respond with the FULL corrected list as strict JSON
only, no prose, no markdown fences: {"items": [{"name": "...", "topics": ["..."]}]}
Keep unmentioned items unchanged. Keep or sensibly update topics for edited items."""


def correct_item_list(items: list[dict], instruction: str) -> list[dict]:
    payload = {"items": [{"position": i + 1, **it} for i, it in enumerate(items)],
               "instruction": instruction}
    result = _call(config.MODEL_HAIKU, ITEM_LIST_CORRECTION_SYSTEM,
                    [{"type": "text", "text": json.dumps(payload)}], max_tokens=1536)
    return result.get("items", [])


# ── natural-language intent router ───────────────────────────────────────

ROUTE_SYSTEM = """You route a free-text message sent to a personal task-manager bot into one
intent. The user's open tasks (id, title) are provided for reference resolution. Respond with
strict JSON only, no prose, no markdown fences:
{"intent": "...", "minutes": number or null, "list": "store"|"online"|"groceries" or null,
 "query": "..." or null, "task_ids": [..] or null}

Intents:
- "capture": the message describes something to do — a new task or several ("buy milk and call
  the dentist", "caulk the sink"). THIS IS THE DEFAULT: when the message doesn't clearly fit
  another intent, choose capture — nothing is ever lost by saving it as a task.
- "eligible_query": asking what they can/should do with some amount of time ("what can I do in
  30 min", "I have an hour, what should I work on"). Set minutes (convert hours).
- "shopping_list": about to go shopping / asking what to buy ("I'm going to Home Depot",
  "heading to the hardware store" -> list "store"; "grocery run", "at the supermarket" ->
  "groceries"; "ordering online" -> "online"). Set list.
- "find_item": asking where a physical thing is stored ("where are my snowshoes", "which box
  has the screwdrivers"). Set query to the thing sought.
- "mark_done": reporting a task is finished ("6 is done", "#12 done", "finished the caulking
  one"). Resolve against the open-task list and set task_ids. If you cannot confidently
  resolve which task, use capture instead.
- "list_tasks": asking to see their tasks ("show my tasks", "what's on my list").
- "correction_hint": an edit instruction about just-captured tasks sent as a NEW message
  instead of a reply ("merge 1 and 4", "delete the second one", "the third one should say X").
  Do not guess task_ids here; the bot will explain how to reply-correct."""


def route_message(text: str, open_tasks: list[dict]) -> dict:
    payload = {"message": text, "open_tasks": open_tasks}
    result = _call(config.MODEL_HAIKU, ROUTE_SYSTEM,
                    [{"type": "text", "text": json.dumps(payload)}], max_tokens=256)
    if result.get("intent") not in ("capture", "eligible_query", "shopping_list", "find_item",
                                     "mark_done", "list_tasks", "correction_hint"):
        result["intent"] = "capture"
    return result
