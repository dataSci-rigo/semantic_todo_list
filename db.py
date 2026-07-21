"""SQLite storage — Phase 0 subset of the spec's data model (tasks,
procedures, captures only; entities/inventory/requirements land in Phase 1a)."""
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
"""


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
