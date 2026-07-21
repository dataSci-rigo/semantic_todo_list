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
