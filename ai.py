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
