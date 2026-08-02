"""SQLite storage for the spec's data model: tasks/procedures/captures
(Phase 0) plus entities/inventory/task_requirements/shopping_lists (Phase 1a).
Phase 1b (dependencies, availability_windows) is not yet built."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    description         TEXT,
    parent_task_id      INTEGER REFERENCES tasks(id),
    status              TEXT NOT NULL DEFAULT 'draft',
    classification_complete INTEGER NOT NULL DEFAULT 0,
    urgency             INTEGER,
    interest            INTEGER,
    energy              INTEGER,
    value               INTEGER,
    skill_level_required INTEGER,
    estimated_active_minutes  INTEGER,
    estimated_passive_minutes INTEGER,
    source              TEXT NOT NULL,
    source_message_id   INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS procedures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      INTEGER NOT NULL REFERENCES tasks(id),
    type         TEXT NOT NULL,
    title        TEXT,
    content_json TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id              INTEGER NOT NULL,
    thread_id            INTEGER,
    inbound_message_id   INTEGER NOT NULL,
    bot_message_id       INTEGER,
    awaiting_other_message_id INTEGER,
    input_type           TEXT NOT NULL,
    raw_text              TEXT,
    image_file_id        TEXT,
    model_used            TEXT,
    ai_response_json      TEXT,
    linked_task_ids       TEXT,
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    type           TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    notes          TEXT,
    created_at     TEXT NOT NULL,
    UNIQUE(type, canonical_name)
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    alias     TEXT NOT NULL,
    UNIQUE(entity_id, alias)
);

CREATE TABLE IF NOT EXISTS task_requirements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id),
    entity_id  INTEGER NOT NULL REFERENCES entities(id),
    kind       TEXT NOT NULL,
    level      INTEGER,
    satisfied  INTEGER NOT NULL DEFAULT 0,
    note       TEXT,
    UNIQUE(task_id, entity_id)
);

CREATE TABLE IF NOT EXISTS inventory (
    entity_id        INTEGER PRIMARY KEY REFERENCES entities(id),
    on_hand          INTEGER NOT NULL DEFAULT 0,
    last_confirmed_at TEXT
);

CREATE TABLE IF NOT EXISTS shopping_lists (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS shopping_list_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id   INTEGER NOT NULL REFERENCES shopping_lists(id),
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    task_id   INTEGER REFERENCES tasks(id),
    purchased INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(list_id, entity_id, task_id)
);

CREATE TABLE IF NOT EXISTS dependencies (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            INTEGER NOT NULL REFERENCES tasks(id),
    depends_on_task_id INTEGER REFERENCES tasks(id),
    type               TEXT NOT NULL,
    note               TEXT,
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS availability_windows (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    duration_minutes INTEGER NOT NULL,
    location         TEXT,
    notes            TEXT,
    raw_text         TEXT,
    created_at       TEXT NOT NULL
);
"""

SUPPLY_STORE_LIST = "supply store run"
ONLINE_LIST = "online shopping"
GROCERY_LIST = "groceries"


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
        if "category" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN category TEXT")
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── tasks ──────────────────────────────────────────────────────────────────

def create_task(title: str, description: str | None, source: str,
                 source_message_id: int | None) -> int:
    conn = _connect()
    try:
        now = _now()
        cur = conn.execute(
            "INSERT INTO tasks (title, description, status, source, source_message_id, created_at, updated_at) "
            "VALUES (?, ?, 'draft', ?, ?, ?, ?)",
            (title, description, source, source_message_id, now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_task(task_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    finally:
        conn.close()


def get_all_tasks(exclude_status: tuple[str, ...] = ("done", "dropped"),
                   category: str | None = None, order_by: str = "id") -> list[sqlite3.Row]:
    conn = _connect()
    try:
        placeholders = ",".join("?" * len(exclude_status))
        query = f"SELECT * FROM tasks WHERE status NOT IN ({placeholders})"
        params: list = list(exclude_status)
        if category is not None:
            query += " AND category LIKE ?"
            params.append(f"%{category}%")
        if order_by in ("urgency", "interest", "energy", "value"):
            query += f" ORDER BY {order_by} DESC, id"
        else:
            query += " ORDER BY id"
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def get_distinct_categories() -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT DISTINCT category FROM tasks WHERE category IS NOT NULL ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]
    finally:
        conn.close()


def get_tasks(task_ids: list[int]) -> list[sqlite3.Row]:
    if not task_ids:
        return []
    conn = _connect()
    try:
        placeholders = ",".join("?" * len(task_ids))
        return conn.execute(
            f"SELECT * FROM tasks WHERE id IN ({placeholders})", task_ids
        ).fetchall()
    finally:
        conn.close()


def update_task(task_id: int, **fields) -> None:
    if not fields:
        return
    conn = _connect()
    try:
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", (*fields.values(), task_id))
        conn.commit()
    finally:
        conn.close()


def delete_task(task_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


# ── procedures ─────────────────────────────────────────────────────────────

def create_procedure(task_id: int, proc_type: str, title: str | None, content: dict) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO procedures (task_id, type, title, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, proc_type, title, json.dumps(content), _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_procedure(procedure_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM procedures WHERE id = ?", (procedure_id,)).fetchone()
    finally:
        conn.close()


def get_procedure_by_task(task_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM procedures WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task_id,)
        ).fetchone()
    finally:
        conn.close()


def update_procedure_content(procedure_id: int, content: dict) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE procedures SET content_json = ? WHERE id = ?",
            (json.dumps(content), procedure_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── captures ───────────────────────────────────────────────────────────────

def create_capture(chat_id: int, thread_id: int | None, inbound_message_id: int,
                    input_type: str, raw_text: str | None = None,
                    image_file_id: str | None = None) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO captures (chat_id, thread_id, inbound_message_id, input_type, raw_text, image_file_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, thread_id, inbound_message_id, input_type, raw_text, image_file_id, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def finalize_capture(capture_id: int, model_used: str, ai_response: dict,
                      linked_task_ids: list[int], bot_message_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE captures SET model_used = ?, ai_response_json = ?, linked_task_ids = ?, "
            "bot_message_id = ?, awaiting_other_message_id = NULL WHERE id = ?",
            (model_used, json.dumps(ai_response), json.dumps(linked_task_ids), bot_message_id, capture_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_awaiting_other(capture_id: int, prompt_message_id: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE captures SET awaiting_other_message_id = ? WHERE id = ?",
            (prompt_message_id, capture_id),
        )
        conn.commit()
    finally:
        conn.close()


def find_capture_by_awaiting_other(message_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM captures WHERE awaiting_other_message_id = ?", (message_id,)
        ).fetchone()
    finally:
        conn.close()


def find_capture_by_bot_message(message_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM captures WHERE bot_message_id = ?", (message_id,)
        ).fetchone()
    finally:
        conn.close()


def get_capture(capture_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    finally:
        conn.close()


# ── entities ───────────────────────────────────────────────────────────────

def get_entities_by_type(entity_type: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, canonical_name FROM entities WHERE type = ?", (entity_type,)
        ).fetchall()
        out = []
        for r in rows:
            aliases = [a["alias"] for a in conn.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id = ?", (r["id"],)
            ).fetchall()]
            out.append({"id": r["id"], "canonical_name": r["canonical_name"], "aliases": aliases})
        return out
    finally:
        conn.close()


def get_entity(entity_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
    finally:
        conn.close()


def create_entity(entity_type: str, canonical_name: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO entities (type, canonical_name, created_at) VALUES (?, ?, ?)",
            (entity_type, canonical_name, _now()),
        )
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        row = conn.execute(
            "SELECT id FROM entities WHERE type = ? AND canonical_name = ?",
            (entity_type, canonical_name),
        ).fetchone()
        return row["id"]
    finally:
        conn.close()


# ── task_requirements ────────────────────────────────────────────────────

def create_requirement(task_id: int, entity_id: int, kind: str, level: int | None = None,
                        satisfied: bool = False, note: str | None = None) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO task_requirements (task_id, entity_id, kind, level, satisfied, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, entity_id, kind, level, int(satisfied), note),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def set_requirement_satisfied(task_id: int, entity_id: int, satisfied: bool) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE task_requirements SET satisfied = ? WHERE task_id = ? AND entity_id = ?",
            (int(satisfied), task_id, entity_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_requirement(task_id: int, entity_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM task_requirements WHERE task_id = ? AND entity_id = ?",
            (task_id, entity_id),
        ).fetchone()
    finally:
        conn.close()


def get_requirements_for_task(task_id: int) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT tr.*, e.canonical_name FROM task_requirements tr "
            "JOIN entities e ON e.id = tr.entity_id WHERE tr.task_id = ?",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()


# ── inventory ─────────────────────────────────────────────────────────────

def get_inventory(entity_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM inventory WHERE entity_id = ?", (entity_id,)).fetchone()
    finally:
        conn.close()


def set_inventory(entity_id: int, on_hand: bool) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO inventory (entity_id, on_hand, last_confirmed_at) VALUES (?, ?, ?) "
            "ON CONFLICT(entity_id) DO UPDATE SET on_hand = excluded.on_hand, "
            "last_confirmed_at = excluded.last_confirmed_at",
            (entity_id, int(on_hand), _now()),
        )
        conn.commit()
    finally:
        conn.close()


# ── shopping lists ────────────────────────────────────────────────────────

def ensure_shopping_list(name: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute("INSERT OR IGNORE INTO shopping_lists (name) VALUES (?)", (name,))
        conn.commit()
        if cur.lastrowid:
            return cur.lastrowid
        return conn.execute("SELECT id FROM shopping_lists WHERE name = ?", (name,)).fetchone()["id"]
    finally:
        conn.close()


def add_shopping_item(list_name: str, entity_id: int, task_id: int | None) -> None:
    list_id = ensure_shopping_list(list_name)
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO shopping_list_items (list_id, entity_id, task_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (list_id, entity_id, task_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_shopping_list_items(list_name: str, unpurchased_only: bool = True) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        query = (
            "SELECT sli.*, e.canonical_name FROM shopping_list_items sli "
            "JOIN shopping_lists sl ON sl.id = sli.list_id "
            "JOIN entities e ON e.id = sli.entity_id "
            "WHERE sl.name = ?"
        )
        if unpurchased_only:
            query += " AND sli.purchased = 0"
        return conn.execute(query, (list_name,)).fetchall()
    finally:
        conn.close()


def remove_shopping_item(list_name: str, entity_id: int, task_id: int | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "DELETE FROM shopping_list_items WHERE entity_id = ? AND task_id IS ? AND "
            "list_id = (SELECT id FROM shopping_lists WHERE name = ?)",
            (entity_id, task_id, list_name),
        )
        conn.commit()
    finally:
        conn.close()


def mark_purchased(item_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE shopping_list_items SET purchased = 1 WHERE id = ?", (item_id,))
        conn.commit()
    finally:
        conn.close()


# ── dependencies ──────────────────────────────────────────────────────────

def create_dependency(task_id: int, depends_on_task_id: int | None, dep_type: str,
                       note: str | None = None) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO dependencies (task_id, depends_on_task_id, type, note, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, depends_on_task_id, dep_type, note, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_dependencies_for_task(task_id: int) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM dependencies WHERE task_id = ?", (task_id,)
        ).fetchall()
    finally:
        conn.close()


# ── availability windows ─────────────────────────────────────────────────

def create_availability_window(duration_minutes: int, location: str | None,
                                notes: str | None, raw_text: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO availability_windows (duration_minutes, location, notes, raw_text, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (duration_minutes, location, notes, raw_text, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
