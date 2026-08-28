"""Bulk ingestion of script_data scrapes (Google Tasks + Google Keep) via a
claude.ai (Fable) round-trip:

1. clean_* functions parse the raw scrape JSONs deterministically.
2. filter_keep_actionable() has Haiku pull real to-dos out of Keep notes.
3. build_grouping_prompt() emits a paste-ready prompt for claude.ai that
   returns projects + subtasks in a strict checkbox format.
4. parse_grouped_output() parses Fable's reply (pasted back or uploaded).
5. import_projects() writes projects as parent tasks + subtasks
   (tasks.parent_task_id), deduping against existing open tasks.

Also holds the prioritization round-trip prompt (build_priority_prompt) and
its parser (parse_priority_output) — same paste-back mechanism.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import config
import db

# NOTE: `ai` (anthropic package) is imported lazily inside the functions that
# need it, so the VM panel — whose venv has no anthropic — can import this
# module for parse/import duties.

# ── scrape cleaning ──────────────────────────────────────────────────────

_JUNK_TOKENS = {"details", "lightbulb_outline", ""}
_WEEKS_AGO = re.compile(r"^\d+\s+weeks?\s+ago$", re.I)


def _clean_open_text(text: str) -> str | None:
    parts = [p.strip() for p in text.split("|")]
    parts = parts[1:]  # drop "Mark completed"
    kept, skipping_location = [], False
    for p in parts:
        low = p.lower()
        if low.startswith("location:"):
            skipping_location = True
            continue
        if skipping_location and (p.startswith("•") or low.startswith(("name:", "display address:",
                                                                        "latitude:", "longitude:", "radius:"))):
            continue
        skipping_location = False
        if low in _JUNK_TOKENS or _WEEKS_AGO.match(p) or low == "lightbulb_outline":
            continue
        kept.append(p.replace("lightbulb_outline", "").strip())
    return " | ".join(k for k in kept if k) or None


def _clean_completed_text(text: str) -> str | None:
    body = text[len("Mark uncompleted"):]
    body = body.split("deleteDelete task")[0]
    body = re.sub(r"Completed:.*$", "", body)
    body = body.replace("lightbulb_outline", "").strip()
    return body or None


def clean_google_tasks_scrape(path: str | Path) -> list[dict]:
    """Returns [{"source_list": name, "text": cleaned, "completed": bool}]."""
    return clean_from_parsed(json.loads(Path(path).read_text()))


def clean_from_parsed(data: dict) -> list[dict]:
    items = []
    for lst in data.get("lists", []):
        for task in lst.get("tasks", []):
            text = task.get("text", "")
            if text.startswith("Mark completed"):
                cleaned = _clean_open_text(text)
                completed = False
            elif text.startswith("Mark uncompleted"):
                cleaned = _clean_completed_text(text)
                completed = True
            else:
                continue  # "syncLoad more" and other UI junk
            if cleaned:
                items.append({"source_list": lst["name"], "text": cleaned, "completed": completed})
    return items


def load_keep_notes(notes_dir: str | Path) -> list[dict]:
    """Reads keep_notes/<ts>/*.md into [{"file", "title", "body"}], skipping
    empty shells."""
    notes = []
    for md in sorted(Path(notes_dir).glob("*.md")):
        text = md.read_text(errors="replace")
        lines = text.splitlines()
        title = lines[0].lstrip("# ").strip() if lines else ""
        if title == "(untitled)":
            title = ""
        body = "\n".join(lines[1:]).strip()
        if not body or body == "Take a note…":
            continue
        notes.append({"file": md.name, "title": title, "body": body[:600]})
    return notes


KEEP_FILTER_SYSTEM = """You scan personal Google Keep notes for ACTIONABLE to-dos. Most notes
are not tasks (songs, quotes, musings, reference links) — skip those entirely. A note that is
or contains a to-do list can yield several items. Respond with strict JSON only, no prose, no
markdown fences: {"items": [{"note": "<file>", "task": "short imperative to-do"}]}
Return an empty list if the batch has no actionable to-dos. Do not invent tasks; only extract
what the person clearly intended to do."""


def filter_keep_actionable(notes: list[dict], batch_size: int = 25,
                            progress=None) -> list[dict]:
    """Haiku-filters notes to actionable items: [{"note", "task"}]."""
    import ai
    out = []
    for i in range(0, len(notes), batch_size):
        batch = notes[i:i + batch_size]
        try:
            result = ai._call(config.MODEL_HAIKU, KEEP_FILTER_SYSTEM,
                               [{"type": "text", "text": json.dumps({"notes": batch})}],
                               max_tokens=2048)
            out.extend(result.get("items", []))
        except Exception as e:
            print(f"  keep filter batch {i // batch_size} error: {e}")
        if progress:
            progress(min(i + batch_size, len(notes)), len(notes))
    return out


# ── grouping prompt (claude.ai round-trip #1) ────────────────────────────

GROUPING_FORMAT_SPEC = """PROJECT: <project name>
- [ ] <open task, short imperative>
- [x] <already-completed task>"""


def build_grouping_prompt(task_items: list[dict], keep_items: list[dict]) -> str:
    lines = [
        "I'm consolidating years of scattered Google Tasks and Google Keep to-dos into a",
        "task manager that organizes work as PROJECTS containing SUBTASKS. Group everything",
        "below into projects for me.",
        "",
        "Rules:",
        "- Produce roughly 8-15 projects. Use the original list names (work, Travel, DIY,",
        "  Plants...) where they make sense; invent clear names for the numbered lists.",
        "- Every to-do becomes one short imperative subtask under exactly one project.",
        "- Split multi-item blobs (separated by '·' or '|') into individual subtasks.",
        "- Merge duplicates and near-duplicates into one subtask.",
        "- Keep completed items (marked DONE below) and mark them [x]; they matter for history.",
        "- Drop pure junk: OCR garbage, UI fragments, notes that aren't really to-dos.",
        "- Skip anything you can't make sense of rather than guessing wildly.",
        "",
        "Reply with ONLY this exact format, no commentary before or after:",
        "",
        GROUPING_FORMAT_SPEC,
        "",
        "=" * 60,
        "DATA — Google Tasks (grouped by their original list):",
        "",
    ]
    by_list: dict[str, list[dict]] = {}
    for it in task_items:
        by_list.setdefault(it["source_list"], []).append(it)
    for name, items in by_list.items():
        lines.append(f"[list: {name}]")
        for it in items:
            flag = "DONE " if it["completed"] else ""
            lines.append(f"  - {flag}{it['text']}")
        lines.append("")
    if keep_items:
        lines.append("DATA — extracted from Google Keep notes:")
        lines.append("")
        for it in keep_items:
            lines.append(f"  - {it['task']}")
    return "\n".join(lines)


# ── parsing Fable's grouped reply ────────────────────────────────────────

_PROJECT_RE = re.compile(r"^\s*PROJECT:\s*(.+?)\s*$", re.I)
_ITEM_RE = re.compile(r"^\s*[-*]\s*\[( |x|X)\]\s*(.+?)\s*$")


def parse_grouped_output(text: str) -> list[dict]:
    """Returns [{"name": project, "items": [{"title", "completed"}]}]."""
    projects: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        m = _PROJECT_RE.match(line)
        if m:
            current = {"name": m.group(1), "items": []}
            projects.append(current)
            continue
        m = _ITEM_RE.match(line)
        if m and current is not None:
            current["items"].append({"title": m.group(2),
                                      "completed": m.group(1).lower() == "x"})
    return [p for p in projects if p["items"]]


def import_project(project: dict) -> dict:
    """Creates one parent task + subtasks. Dedupes subtasks (case-insensitive
    title match) against ALL existing tasks. Imported tasks skip the
    classification flow. Returns counts."""
    existing = {r["title"].strip().lower() for r in db.get_all_tasks(exclude_status=())}
    parent_id = None
    created = skipped = 0
    for item in project["items"]:
        key = item["title"].strip().lower()
        if key in existing:
            skipped += 1
            continue
        if parent_id is None:
            parent_id = db.create_task(project["name"], "imported project", "import", None)
            db.update_task(parent_id, category=project["name"], classification_complete=1)
        task_id = db.create_task(item["title"], None, "import", None)
        db.update_task(task_id, parent_task_id=parent_id, category=project["name"],
                        status="done" if item["completed"] else "draft")
        existing.add(key)
        created += 1
    return {"created": created, "skipped": skipped, "parent_id": parent_id}


# ── prioritization prompt (claude.ai round-trip #2) ──────────────────────

def build_priority_prompt() -> str:
    rows = db.get_all_tasks()
    lines = [
        "Prioritize my personal task list. Consider urgency, long-term value, and momentum",
        "(quick wins that unblock other things rank higher). I'm an ADHD adult balancing",
        "family, career, and home projects.",
        "",
        "Reply with ONLY a numbered list, highest priority first, one task per line,",
        "using the exact ids, in this format (a short reason after the dash is welcome):",
        "1. #12 — reason",
        "2. #7 — reason",
        "",
        "My open tasks:",
        "",
    ]
    for r in rows:
        if db.get_subtasks(r["id"]):
            continue  # project containers aren't prioritizable work items
        meta = []
        if r["category"]:
            meta.append(r["category"])
        if r["classification_complete"]:
            meta.append(f"urgency {r['urgency']}/5, interest {r['interest']}/5, "
                        f"energy {r['energy']}/5, value {r['value']}/5")
        if r["estimated_active_minutes"] is not None:
            meta.append(f"~{r['estimated_active_minutes']} min")
        if r["parent_task_id"]:
            parent = db.get_task(r["parent_task_id"])
            if parent:
                meta.append(f"part of: {parent['title']}")
        suffix = f"  ({'; '.join(meta)})" if meta else ""
        lines.append(f"#{r['id']} {r['title']}{suffix}")
    return "\n".join(lines)


_ID_RE = re.compile(r"#(\d+)")


def parse_priority_output(text: str) -> list[int]:
    """Task ids in order of appearance = priority order. Dedupes, keeps first."""
    seen, order = set(), []
    for m in _ID_RE.finditer(text):
        tid = int(m.group(1))
        if tid not in seen:
            seen.add(tid)
            order.append(tid)
    return order


def apply_priorities(ordered_ids: list[int]) -> int:
    applied = 0
    for rank, task_id in enumerate(ordered_ids, 1):
        if db.get_task(task_id) is not None:
            db.update_task(task_id, priority=rank)
            applied += 1
    return applied
