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

CREATE TABLE IF NOT EXISTS google_task_links (
    task_id         INTEGER PRIMARY KEY REFERENCES tasks(id),
    google_task_id  TEXT NOT NULL UNIQUE,
    google_list_id  TEXT NOT NULL,
    google_updated  TEXT,
    last_synced_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_locations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    photo_file_id TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_boxes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    number        INTEGER NOT NULL UNIQUE,
    label         TEXT,
    photo_file_id TEXT,
    location_id   INTEGER REFERENCES storage_locations(id),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_storage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   INTEGER NOT NULL REFERENCES entities(id),
    box_id      INTEGER REFERENCES storage_boxes(id),
    location_id INTEGER REFERENCES storage_locations(id),
    created_at  TEXT NOT NULL
);
"""

SUPPLY_STORE_LIST = "supply store run"
ONLINE_LIST = "online shopping"
GROCERY_LIST = "groceries"


def _connect() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
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

        if "priority" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER")

        link_cols = {row["name"] for row in conn.execute("PRAGMA table_info(google_task_links)")}
        if "version" not in link_cols:
            conn.execute("ALTER TABLE google_task_links ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
        if "foreign_json" not in link_cols:
            conn.execute("ALTER TABLE google_task_links ADD COLUMN foreign_json TEXT")
        if "locked_by_us" not in link_cols:
            conn.execute("ALTER TABLE google_task_links ADD COLUMN locked_by_us INTEGER NOT NULL DEFAULT 0")

        aw_cols = {row["name"] for row in conn.execute("PRAGMA table_info(availability_windows)")}
        if "recurring" not in aw_cols:
            conn.execute("ALTER TABLE availability_windows ADD COLUMN recurring INTEGER NOT NULL DEFAULT 0")
        if "day_of_week" not in aw_cols:
            conn.execute("ALTER TABLE availability_windows ADD COLUMN day_of_week INTEGER")
        if "start_time" not in aw_cols:
            conn.execute("ALTER TABLE availability_windows ADD COLUMN start_time TEXT")
        if "last_notified_at" not in aw_cols:
            conn.execute("ALTER TABLE availability_windows ADD COLUMN last_notified_at TEXT")

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


def get_tasks_by_priority(limit: int = 10) -> list[sqlite3.Row]:
    """Open leaf tasks (not project parents) ordered by absolute priority;
    unprioritized tasks fall back after, ordered by u+v+i-e score."""
    conn = _connect()
    try:
        return conn.execute(
            "SELECT t.* FROM tasks t "
            "WHERE t.status NOT IN ('done', 'dropped') "
            "AND NOT EXISTS (SELECT 1 FROM tasks c WHERE c.parent_task_id = t.id) "
            "ORDER BY CASE WHEN t.priority IS NULL THEN 1 ELSE 0 END, t.priority, "
            "(COALESCE(t.urgency,0)+COALESCE(t.value,0)+COALESCE(t.interest,0)-COALESCE(t.energy,0)) DESC, "
            "t.id LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def get_subtasks(parent_task_id: int) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY id", (parent_task_id,)
        ).fetchall()
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


def get_dependents(task_id: int) -> list[sqlite3.Row]:
    """Tasks with a finish-to-start dependency on task_id — i.e. tasks that
    were waiting on task_id to finish."""
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM dependencies WHERE depends_on_task_id = ? AND type = 'finish-to-start'",
            (task_id,),
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


def create_recurring_window(day_of_week: int, start_time: str, duration_minutes: int,
                             location: str | None, notes: str | None, raw_text: str) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO availability_windows "
            "(duration_minutes, location, notes, raw_text, recurring, day_of_week, start_time, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
            (duration_minutes, location, notes, raw_text, day_of_week, start_time, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_recurring_windows() -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM availability_windows WHERE recurring = 1").fetchall()
    finally:
        conn.close()


def delete_availability_window(window_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM availability_windows WHERE id = ?", (window_id,))
        conn.commit()
    finally:
        conn.close()


def mark_window_notified(window_id: int, date_str: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE availability_windows SET last_notified_at = ? WHERE id = ?",
            (date_str, window_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── google tasks sync links ──────────────────────────────────────────────

def link_google_task(task_id: int, google_task_id: str, google_list_id: str,
                      google_updated: str | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO google_task_links (task_id, google_task_id, google_list_id, google_updated, last_synced_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET google_task_id = excluded.google_task_id, "
            "google_list_id = excluded.google_list_id, google_updated = excluded.google_updated, "
            "last_synced_at = excluded.last_synced_at",
            (task_id, google_task_id, google_list_id, google_updated, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def touch_google_link(task_id: int, google_updated: str | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE google_task_links SET google_updated = ?, last_synced_at = ? WHERE task_id = ?",
            (google_updated, _now(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_link_bus_state(task_id: int, version: int, foreign_json: str | None = None,
                           google_list_id: str | None = None,
                           google_task_id: str | None = None) -> None:
    conn = _connect()
    try:
        sets = ["version = ?", "last_synced_at = ?"]
        params: list = [version, _now()]
        if foreign_json is not None:
            sets.append("foreign_json = ?")
            params.append(foreign_json)
        if google_list_id is not None:
            sets.append("google_list_id = ?")
            params.append(google_list_id)
        if google_task_id is not None:
            sets.append("google_task_id = ?")
            params.append(google_task_id)
        params.append(task_id)
        conn.execute(f"UPDATE google_task_links SET {', '.join(sets)} WHERE task_id = ?", params)
        conn.commit()
    finally:
        conn.close()


def set_link_locked(task_id: int, locked: bool) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE google_task_links SET locked_by_us = ? WHERE task_id = ?",
                      (int(locked), task_id))
        conn.commit()
    finally:
        conn.close()


def get_link_by_task(task_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM google_task_links WHERE task_id = ?", (task_id,)).fetchone()
    finally:
        conn.close()


def get_link_by_google_id(google_task_id: str) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM google_task_links WHERE google_task_id = ?", (google_task_id,)
        ).fetchone()
    finally:
        conn.close()


def get_unlinked_tasks() -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT t.* FROM tasks t LEFT JOIN google_task_links g ON g.task_id = t.id "
            "WHERE g.task_id IS NULL AND t.status NOT IN ('dropped')"
        ).fetchall()
    finally:
        conn.close()


def get_all_links() -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM google_task_links").fetchall()
    finally:
        conn.close()


# ── storage tracker (boxes, locations, item placement) ───────────────────

def create_storage_location(name: str, photo_file_id: str | None) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO storage_locations (name, photo_file_id, created_at) VALUES (?, ?, ?)",
            (name, photo_file_id, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_storage_locations() -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute("SELECT * FROM storage_locations ORDER BY id").fetchall()
    finally:
        conn.close()


def get_storage_location(location_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT * FROM storage_locations WHERE id = ?", (location_id,)
        ).fetchone()
    finally:
        conn.close()


def rename_storage_location(location_id: int, name: str) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE storage_locations SET name = ? WHERE id = ?", (name, location_id))
        conn.commit()
    finally:
        conn.close()


def create_storage_box(photo_file_id: str | None, location_id: int | None,
                        label: str | None = None) -> int:
    """Auto-assigns the next box number (max+1) so it matches a number the
    user writes on the physical box. Returns the box row id."""
    conn = _connect()
    try:
        row = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 AS n FROM storage_boxes").fetchone()
        cur = conn.execute(
            "INSERT INTO storage_boxes (number, label, photo_file_id, location_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["n"], label, photo_file_id, location_id, _now()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_storage_boxes() -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT b.*, l.name AS location_name FROM storage_boxes b "
            "LEFT JOIN storage_locations l ON l.id = b.location_id ORDER BY b.number"
        ).fetchall()
    finally:
        conn.close()


def get_box(box_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT b.*, l.name AS location_name, l.photo_file_id AS location_photo_file_id "
            "FROM storage_boxes b LEFT JOIN storage_locations l ON l.id = b.location_id "
            "WHERE b.id = ?", (box_id,)
        ).fetchone()
    finally:
        conn.close()


def get_box_by_number(number: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT b.*, l.name AS location_name, l.photo_file_id AS location_photo_file_id "
            "FROM storage_boxes b LEFT JOIN storage_locations l ON l.id = b.location_id "
            "WHERE b.number = ?", (number,)
        ).fetchone()
    finally:
        conn.close()


def set_box_location(box_id: int, location_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("UPDATE storage_boxes SET location_id = ? WHERE id = ?", (location_id, box_id))
        conn.commit()
    finally:
        conn.close()


def store_item(entity_id: int, box_id: int | None, location_id: int | None) -> None:
    """Record where an item lives. Replaces any previous placement of the
    same entity — an item has one home at a time."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM item_storage WHERE entity_id = ?", (entity_id,))
        conn.execute(
            "INSERT INTO item_storage (entity_id, box_id, location_id, created_at) VALUES (?, ?, ?, ?)",
            (entity_id, box_id, location_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def get_item_storage(entity_id: int) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT s.*, e.canonical_name, b.number AS box_number, b.label AS box_label, "
            "b.photo_file_id AS box_photo_file_id, "
            "COALESCE(bl.id, dl.id) AS resolved_location_id, "
            "COALESCE(bl.name, dl.name) AS location_name, "
            "COALESCE(bl.photo_file_id, dl.photo_file_id) AS location_photo_file_id "
            "FROM item_storage s "
            "JOIN entities e ON e.id = s.entity_id "
            "LEFT JOIN storage_boxes b ON b.id = s.box_id "
            "LEFT JOIN storage_locations bl ON bl.id = b.location_id "
            "LEFT JOIN storage_locations dl ON dl.id = s.location_id "
            "WHERE s.entity_id = ?", (entity_id,)
        ).fetchone()
    finally:
        conn.close()


def get_items_in_box(box_id: int) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT s.*, e.canonical_name FROM item_storage s "
            "JOIN entities e ON e.id = s.entity_id WHERE s.box_id = ? ORDER BY e.canonical_name",
            (box_id,),
        ).fetchall()
    finally:
        conn.close()


def get_all_stored_items() -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(
            "SELECT s.entity_id, e.canonical_name, b.number AS box_number, b.label AS box_label, "
            "COALESCE(bl.name, dl.name) AS location_name "
            "FROM item_storage s "
            "JOIN entities e ON e.id = s.entity_id "
            "LEFT JOIN storage_boxes b ON b.id = s.box_id "
            "LEFT JOIN storage_locations bl ON bl.id = b.location_id "
            "LEFT JOIN storage_locations dl ON dl.id = s.location_id "
            "ORDER BY e.canonical_name"
        ).fetchall()
    finally:
        conn.close()


def get_tasks_requiring(entity_id: int) -> list[sqlite3.Row]:
    """Open tasks that list this entity as a requirement."""
    conn = _connect()
    try:
        return conn.execute(
            "SELECT t.* FROM tasks t JOIN task_requirements tr ON tr.task_id = t.id "
            "WHERE tr.entity_id = ? AND t.status NOT IN ('done', 'dropped') ORDER BY t.id",
            (entity_id,),
        ).fetchall()
    finally:
        conn.close()


def add_entity_alias(entity_id: int, alias: str) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO entity_aliases (entity_id, alias) VALUES (?, ?)",
            (entity_id, alias.strip().lower()),
        )
        conn.commit()
    finally:
        conn.close()


def get_aliases(entity_id: int) -> list[str]:
    conn = _connect()
    try:
        return [r["alias"] for r in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ?", (entity_id,)
        ).fetchall()]
    finally:
        conn.close()
