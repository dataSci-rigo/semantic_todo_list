"""Growth tables (gb_*) in STM's data/tasks.db.

Same file as STM's tables so the priority read can join tasks ⋈
google_task_links, but a separate module with its own connection helper.
Never imports anthropic or telegram_api, so the panel can import it."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import config as stm

from . import clock

SCHEMA = """
CREATE TABLE IF NOT EXISTS gb_users (
    user_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    thread_id INTEGER,
    tz TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    morning_time TEXT NOT NULL DEFAULT '07:30',
    evening_time TEXT NOT NULL DEFAULT '21:00',
    refocus_windows TEXT NOT NULL DEFAULT '["10:30","13:30","16:30"]',
    survival INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    onboarding_step TEXT NOT NULL DEFAULT 'new',
    tier TEXT NOT NULL DEFAULT 't30',
    obs_started_on TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gb_core_data (
    user_id INTEGER NOT NULL,
    field TEXT NOT NULL,
    value TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, field)
);
CREATE TABLE IF NOT EXISTS gb_days (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    planned_at TEXT,
    survival INTEGER NOT NULL DEFAULT 0,
    capacity INTEGER,
    sleep TEXT,
    tier TEXT,
    p_list TEXT,
    p_source TEXT,
    rest_budget TEXT,
    wont TEXT,
    wont_kept TEXT,
    failure_point TEXT,
    easier_tomorrow TEXT,
    closed_at TEXT,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS gb_touches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    kind TEXT NOT NULL,
    slot INTEGER,
    challenge_id INTEGER,
    chat_id INTEGER NOT NULL,
    thread_id INTEGER,
    due_at TEXT,
    fired_at TEXT,
    expires_at TEXT,
    completed_at TEXT,
    message_id INTEGER,
    step TEXT,
    state TEXT NOT NULL DEFAULT '{}',
    elective_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'scheduled',
    send_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (user_id, day, kind, slot)
);
CREATE INDEX IF NOT EXISTS gb_touches_due ON gb_touches (status, due_at);
CREATE INDEX IF NOT EXISTS gb_touches_msg ON gb_touches (message_id);
CREATE TABLE IF NOT EXISTS gb_pending_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    thread_id INTEGER,
    message_id INTEGER NOT NULL,
    touch_id INTEGER NOT NULL,
    step TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gb_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    ts TEXT NOT NULL,
    name TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS gb_events_name ON gb_events (user_id, name, day);
CREATE TABLE IF NOT EXISTS gb_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    ts TEXT NOT NULL,
    touch_id INTEGER,
    module_id TEXT,
    field TEXT NOT NULL,
    value TEXT,
    value_num REAL,
    rubric_json TEXT
);
CREATE TABLE IF NOT EXISTS gb_challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    n INTEGER NOT NULL,
    tier TEXT NOT NULL DEFAULT 't30',
    task_id INTEGER,
    task_title TEXT,
    p INTEGER,
    done_looks_like TEXT,
    why_not_p5 TEXT,
    est_min INTEGER,
    started_at TEXT,
    ended_at TEXT,
    actual_min INTEGER,
    moved INTEGER,
    resume_note TEXT,
    next_step TEXT,
    outcome TEXT,
    premortem TEXT,
    error_log TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gb_refocus_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    touch_id INTEGER,
    answered_at TEXT NOT NULL,
    on_kind TEXT NOT NULL,
    on_p INTEGER,
    p1_task_id INTEGER,
    p1_title TEXT,
    rest_mode TEXT,
    excuse_log_id INTEGER,
    interest_flag INTEGER NOT NULL DEFAULT 0,
    minutes_since_prev INTEGER
);
CREATE TABLE IF NOT EXISTS gb_excuses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    family INTEGER NOT NULL,
    text TEXT NOT NULL,
    size TEXT NOT NULL DEFAULT 'small',
    counter_move TEXT,
    follow_up TEXT,
    flag TEXT,
    count INTEGER NOT NULL DEFAULT 0,
    last_used TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    seed TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, family, text)
);
CREATE TABLE IF NOT EXISTS gb_excuse_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    excuse_id INTEGER NOT NULL,
    touch_id INTEGER,
    task_id INTEGER,
    task_name TEXT,
    counter_taken TEXT,
    logged_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gb_wins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    difficulty INTEGER,
    domain TEXT,
    source TEXT NOT NULL DEFAULT 'win',
    happened_on TEXT,
    shown_count INTEGER NOT NULL DEFAULT 0,
    last_shown TEXT,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS gb_wins_fts USING fts5(
    text, tags, domain, tokenize='porter unicode61'
);
CREATE TABLE IF NOT EXISTS gb_rest_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    item TEXT NOT NULL,
    budget_min INTEGER,
    started_at TEXT,
    ended_at TEXT,
    actual_min INTEGER,
    mode TEXT NOT NULL DEFAULT 'budgeted',
    restored TEXT,
    timer_id INTEGER
);
CREATE TABLE IF NOT EXISTS gb_timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    thread_id INTEGER,
    kind TEXT NOT NULL,
    seconds INTEGER NOT NULL,
    touch_id INTEGER,
    step TEXT,
    challenge_id INTEGER,
    rest_log_id INTEGER,
    started_at TEXT NOT NULL,
    due_at TEXT NOT NULL,
    fired_at TEXT,
    message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS gb_timers_due ON gb_timers (status, due_at);
CREATE TABLE IF NOT EXISTS gb_levels (
    user_id INTEGER NOT NULL,
    track TEXT NOT NULL,
    level INTEGER NOT NULL,
    reached_at TEXT NOT NULL,
    evidence TEXT,
    PRIMARY KEY (user_id, track, level)
);
CREATE TABLE IF NOT EXISTS gb_snapshots (
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    value REAL,
    taken_at TEXT NOT NULL,
    PRIMARY KEY (user_id, name)
);
CREATE TABLE IF NOT EXISTS gb_activity (
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    touches_done INTEGER NOT NULL DEFAULT 0,
    first_at TEXT,
    last_at TEXT,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS gb_module_exposures (
    user_id INTEGER NOT NULL,
    module_id TEXT NOT NULL,
    exposures INTEGER NOT NULL DEFAULT 0,
    downs INTEGER NOT NULL DEFAULT 0,
    ups INTEGER NOT NULL DEFAULT 0,
    dropped INTEGER NOT NULL DEFAULT 0,
    last_day TEXT,
    PRIMARY KEY (user_id, module_id)
);
CREATE TABLE IF NOT EXISTS gb_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    module_id TEXT,
    entry_id INTEGER,
    vote INTEGER,
    text TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gb_visualizations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    roles TEXT NOT NULL DEFAULT '[]',
    horizon TEXT,
    text TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    shown_count INTEGER NOT NULL DEFAULT 0,
    last_shown TEXT,
    up INTEGER NOT NULL DEFAULT 0,
    dn INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

USER_TABLES = [
    "gb_users", "gb_core_data", "gb_days", "gb_touches", "gb_pending_prompts", "gb_events",
    "gb_entries", "gb_challenges", "gb_refocus_logs", "gb_excuses", "gb_excuse_logs", "gb_wins",
    "gb_rest_logs", "gb_timers", "gb_levels", "gb_snapshots", "gb_activity",
    "gb_module_exposures", "gb_feedback", "gb_visualizations",
]


def _connect() -> sqlite3.Connection:
    stm.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(stm.DB_PATH, timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
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
    return clock.iso(clock.now())


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def loads(s: str | None, default: Any = None) -> Any:
    if s is None:
        return default
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return default


def _insert(table: str, **cols: Any) -> int:
    conn = _connect()
    try:
        keys = ", ".join(cols)
        qs = ", ".join("?" * len(cols))
        cur = conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({qs})", tuple(cols.values()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _update(table: str, where: str, params: tuple, **cols: Any) -> None:
    if not cols:
        return
    conn = _connect()
    try:
        sets = ", ".join(f"{k} = ?" for k in cols)
        conn.execute(f"UPDATE {table} SET {sets} WHERE {where}", (*cols.values(), *params))
        conn.commit()
    finally:
        conn.close()


def _one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    conn = _connect()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def _all(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = _connect()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _exec(sql: str, params: tuple = ()) -> None:
    conn = _connect()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


# ── users ─────────────────────────────────────────────────────────────────

def get_user(user_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_users WHERE user_id = ?", (user_id,))


def ensure_user(user_id: int, chat_id: int, thread_id: int | None, tz: str) -> sqlite3.Row:
    row = get_user(user_id)
    if row is None:
        now = _now()
        _insert("gb_users", user_id=user_id, chat_id=chat_id, thread_id=thread_id, tz=tz,
                created_at=now, updated_at=now)
        row = get_user(user_id)
    return row


def update_user(user_id: int, **fields: Any) -> None:
    fields["updated_at"] = _now()
    _update("gb_users", "user_id = ?", (user_id,), **fields)


def active_users() -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_users WHERE active = 1 ORDER BY user_id")


# ── core data ─────────────────────────────────────────────────────────────

def get_core(user_id: int) -> dict[str, Any]:
    rows = _all("SELECT field, value FROM gb_core_data WHERE user_id = ?", (user_id,))
    return {r["field"]: loads(r["value"], r["value"]) for r in rows}


def set_core(user_id: int, field: str, value: Any) -> None:
    _exec("INSERT INTO gb_core_data (user_id, field, value, updated_at) VALUES (?, ?, ?, ?) "
          "ON CONFLICT(user_id, field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
          (user_id, field, _j(value), _now()))


# ── days ──────────────────────────────────────────────────────────────────

def get_day(user_id: int, day: str) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_days WHERE user_id = ? AND day = ?", (user_id, day))


def ensure_day(user_id: int, day: str, survival: int = 0) -> sqlite3.Row:
    row = get_day(user_id, day)
    if row is None:
        _exec("INSERT OR IGNORE INTO gb_days (user_id, day, survival) VALUES (?, ?, ?)", (user_id, day, survival))
        row = get_day(user_id, day)
    return row


def update_day(user_id: int, day: str, **fields: Any) -> None:
    ensure_day(user_id, day)
    _update("gb_days", "user_id = ? AND day = ?", (user_id, day), **fields)


def recent_days(user_id: int, before_day: str, n: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_days WHERE user_id = ? AND day <= ? ORDER BY day DESC LIMIT ?",
                (user_id, before_day, n))


def active_days(user_id: int, before_day: str, n: int) -> list[str]:
    """The last n days (≤ before_day) with survival off, newest first."""
    rows = _all("SELECT day FROM gb_days WHERE user_id = ? AND day <= ? AND survival = 0 "
                "ORDER BY day DESC LIMIT ?", (user_id, before_day, n))
    return [r["day"] for r in rows]


# ── touches ───────────────────────────────────────────────────────────────

def create_touch(**cols: Any) -> int:
    now = _now()
    cols.setdefault("created_at", now)
    cols.setdefault("updated_at", now)
    return _insert("gb_touches", **cols)


def get_touch(touch_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_touches WHERE id = ?", (touch_id,))


def update_touch(touch_id: int, **fields: Any) -> None:
    fields["updated_at"] = _now()
    _update("gb_touches", "id = ?", (touch_id,), **fields)


def touch_by_message(user_id: int, message_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_touches WHERE user_id = ? AND message_id = ? ORDER BY id DESC LIMIT 1",
                (user_id, message_id))


def touch_exists(user_id: int, day: str, kind: str, slot: int | None) -> bool:
    if slot is None:
        return False
    return _one("SELECT 1 FROM gb_touches WHERE user_id = ? AND day = ? AND kind = ? AND slot = ?",
                (user_id, day, kind, slot)) is not None


def due_touches(now_iso: str, limit: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_touches WHERE status = 'scheduled' AND due_at IS NOT NULL AND due_at <= ? "
                "ORDER BY due_at LIMIT ?", (now_iso, limit))


def failed_touches(limit: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_touches WHERE status = 'send_failed' AND send_attempts < 3 ORDER BY id LIMIT ?",
                (limit,))


def active_touches(user_id: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_touches WHERE user_id = ? AND status = 'active' ORDER BY fired_at DESC, id DESC",
                (user_id,))


def touches_for_day(user_id: int, day: str) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_touches WHERE user_id = ? AND day = ? ORDER BY slot, id", (user_id, day))


def stale_active_touches(now_iso: str) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_touches WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
                (now_iso,))


def next_due_iso() -> str | None:
    row = _one("SELECT MIN(d) AS d FROM (SELECT due_at AS d FROM gb_touches WHERE status = 'scheduled' "
               "AND due_at IS NOT NULL UNION ALL SELECT due_at AS d FROM gb_timers WHERE status = 'running')")
    return row["d"] if row and row["d"] else None


def latest_fired_at(user_id: int) -> str | None:
    row = _one("SELECT MAX(fired_at) AS f FROM gb_touches WHERE user_id = ?", (user_id,))
    return row["f"] if row else None


# ── pending prompts ───────────────────────────────────────────────────────

def add_prompt(user_id: int, chat_id: int, thread_id: int | None, message_id: int,
               touch_id: int, step: str, expires_at: str) -> int:
    _exec("DELETE FROM gb_pending_prompts WHERE touch_id = ?", (touch_id,))
    return _insert("gb_pending_prompts", user_id=user_id, chat_id=chat_id, thread_id=thread_id,
                   message_id=message_id, touch_id=touch_id, step=step, created_at=_now(),
                   expires_at=expires_at)


def prompt_by_message(user_id: int, message_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_pending_prompts WHERE user_id = ? AND message_id = ? ORDER BY id DESC LIMIT 1",
                (user_id, message_id))


def latest_prompt(user_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_pending_prompts WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))


def prompt_for_touch(touch_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_pending_prompts WHERE touch_id = ? ORDER BY id DESC LIMIT 1", (touch_id,))


def delete_prompts_for_touch(touch_id: int) -> None:
    _exec("DELETE FROM gb_pending_prompts WHERE touch_id = ?", (touch_id,))


def expire_prompts(now_iso: str) -> None:
    _exec("DELETE FROM gb_pending_prompts WHERE expires_at <= ?", (now_iso,))


# ── events / entries ──────────────────────────────────────────────────────

def emit(user_id: int, day: str, name: str, meta: dict | None = None) -> int:
    return _insert("gb_events", user_id=user_id, day=day, ts=_now(), name=name, meta_json=_j(meta or {}))


def events(user_id: int, name: str, days: list[str] | None = None) -> list[sqlite3.Row]:
    if days is None:
        return _all("SELECT * FROM gb_events WHERE user_id = ? AND name = ? ORDER BY ts", (user_id, name))
    if not days:
        return []
    qs = ",".join("?" * len(days))
    return _all(f"SELECT * FROM gb_events WHERE user_id = ? AND name = ? AND day IN ({qs}) ORDER BY ts",
                (user_id, name, *days))


def events_for_day(user_id: int, day: str, names: tuple[str, ...]) -> list[sqlite3.Row]:
    qs = ",".join("?" * len(names))
    return _all(f"SELECT * FROM gb_events WHERE user_id = ? AND day = ? AND name IN ({qs}) ORDER BY ts",
                (user_id, day, *names))


def add_entry(user_id: int, day: str, touch_id: int | None, module_id: str | None, field: str,
              value: Any, value_num: float | None = None) -> int:
    return _insert("gb_entries", user_id=user_id, day=day, ts=_now(), touch_id=touch_id, module_id=module_id,
                   field=field, value=_j(value), value_num=value_num)


def set_entry_rubric(entry_id: int, rubric: dict) -> None:
    _update("gb_entries", "id = ?", (entry_id,), rubric_json=_j(rubric))


def entries(user_id: int, field: str | None = None, module_id: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    sql = "SELECT * FROM gb_entries WHERE user_id = ?"
    params: list = [user_id]
    if field:
        sql += " AND field = ?"
        params.append(field)
    if module_id:
        sql += " AND module_id = ?"
        params.append(module_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return _all(sql, tuple(params))


def graded_entries(user_id: int, limit: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_entries WHERE user_id = ? AND rubric_json IS NOT NULL ORDER BY id DESC LIMIT ?",
                (user_id, limit))


# ── challenges ────────────────────────────────────────────────────────────

def create_challenge(user_id: int, day: str, n: int, tier: str, task_id: int | None, task_title: str | None,
                     p: int | None) -> int:
    return _insert("gb_challenges", user_id=user_id, day=day, n=n, tier=tier, task_id=task_id,
                   task_title=task_title, p=p, created_at=_now())


def get_challenge(challenge_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_challenges WHERE id = ?", (challenge_id,))


def update_challenge(challenge_id: int, **fields: Any) -> None:
    _update("gb_challenges", "id = ?", (challenge_id,), **fields)


def challenges_for_day(user_id: int, day: str) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_challenges WHERE user_id = ? AND day = ? ORDER BY n, id", (user_id, day))


def open_challenge(user_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_challenges WHERE user_id = ? AND status = 'started' ORDER BY id DESC LIMIT 1",
                (user_id,))


def recent_ended_challenges(user_id: int, n: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_challenges WHERE user_id = ? AND status = 'ended' ORDER BY ended_at DESC LIMIT ?",
                (user_id, n))


# ── refocus ───────────────────────────────────────────────────────────────

def add_refocus_log(**cols: Any) -> int:
    cols.setdefault("answered_at", _now())
    return _insert("gb_refocus_logs", **cols)


def refocus_logs(user_id: int, days: list[str]) -> list[sqlite3.Row]:
    if not days:
        return []
    qs = ",".join("?" * len(days))
    return _all(f"SELECT * FROM gb_refocus_logs WHERE user_id = ? AND day IN ({qs}) ORDER BY answered_at",
                (user_id, *days))


def last_state_event_ts(user_id: int, day: str) -> str | None:
    """Timestamp of the previous refocus answer / challenge start or end / rest start today."""
    row = _one(
        "SELECT MAX(t) AS t FROM ("
        " SELECT answered_at AS t FROM gb_refocus_logs WHERE user_id = ? AND day = ?"
        " UNION ALL SELECT started_at AS t FROM gb_challenges WHERE user_id = ? AND day = ? AND started_at IS NOT NULL"
        " UNION ALL SELECT ended_at AS t FROM gb_challenges WHERE user_id = ? AND day = ? AND ended_at IS NOT NULL"
        " UNION ALL SELECT started_at AS t FROM gb_rest_logs WHERE user_id = ? AND day = ? AND started_at IS NOT NULL)",
        (user_id, day) * 4)
    return row["t"] if row else None


# ── excuses ───────────────────────────────────────────────────────────────

def excuses_for_family(user_id: int, family: int, limit: int = 6) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_excuses WHERE user_id = ? AND family = ? AND active = 1 "
                "ORDER BY count DESC, id LIMIT ?", (user_id, family, limit))


def all_excuses(user_id: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_excuses WHERE user_id = ? ORDER BY family, count DESC, id", (user_id,))


def get_excuse(excuse_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_excuses WHERE id = ?", (excuse_id,))


def add_excuse(user_id: int, family: int, text: str, size: str = "small", counter_move: str | None = None,
               follow_up: str | None = None, flag: str | None = None, seed: str | None = None) -> int:
    _exec("INSERT OR IGNORE INTO gb_excuses (user_id, family, text, size, counter_move, follow_up, flag, seed, created_at) "
          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
          (user_id, family, text, size, counter_move, follow_up, flag, seed, _now()))
    row = _one("SELECT id FROM gb_excuses WHERE user_id = ? AND family = ? AND text = ?", (user_id, family, text))
    return row["id"]


def excuse_seeded(user_id: int) -> bool:
    return _one("SELECT 1 FROM gb_excuses WHERE user_id = ? AND seed IS NOT NULL LIMIT 1", (user_id,)) is not None


def add_excuse_log(user_id: int, day: str, excuse_id: int, touch_id: int | None, task_id: int | None,
                   task_name: str | None, counter_taken: str | None = None) -> int:
    _exec("UPDATE gb_excuses SET count = count + 1, last_used = ? WHERE id = ?", (_now(), excuse_id))
    return _insert("gb_excuse_logs", user_id=user_id, day=day, excuse_id=excuse_id, touch_id=touch_id,
                   task_id=task_id, task_name=task_name, counter_taken=counter_taken, logged_at=_now())


def update_excuse_log(log_id: int, **fields: Any) -> None:
    _update("gb_excuse_logs", "id = ?", (log_id,), **fields)


def excuse_logs(user_id: int, since_day: str) -> list[sqlite3.Row]:
    return _all("SELECT l.*, e.text, e.family, e.size FROM gb_excuse_logs l JOIN gb_excuses e ON e.id = l.excuse_id "
                "WHERE l.user_id = ? AND l.day >= ? ORDER BY l.logged_at", (user_id, since_day))


# ── wins (Success Bank) ───────────────────────────────────────────────────

def add_win(user_id: int, text: str, source: str, happened_on: str | None, tags: list[str] | None = None,
            difficulty: int | None = None, domain: str | None = None) -> int:
    win_id = _insert("gb_wins", user_id=user_id, text=text, tags=_j(tags or []), difficulty=difficulty,
                     domain=domain, source=source, happened_on=happened_on, created_at=_now())
    _reindex_win(win_id)
    return win_id


def update_win(win_id: int, **fields: Any) -> None:
    if "tags" in fields and not isinstance(fields["tags"], str):
        fields["tags"] = _j(fields["tags"])
    _update("gb_wins", "id = ?", (win_id,), **fields)
    _reindex_win(win_id)


def _reindex_win(win_id: int) -> None:
    row = get_win(win_id)
    if row is None:
        return
    tags = " ".join(loads(row["tags"], []) or [])
    conn = _connect()
    try:
        conn.execute("DELETE FROM gb_wins_fts WHERE rowid = ?", (win_id,))
        conn.execute("INSERT INTO gb_wins_fts (rowid, text, tags, domain) VALUES (?, ?, ?, ?)",
                     (win_id, row["text"], tags, row["domain"] or ""))
        conn.commit()
    finally:
        conn.close()


def get_win(win_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_wins WHERE id = ?", (win_id,))


def wins(user_id: int, limit: int = 20, include_system: bool = False) -> list[sqlite3.Row]:
    sql = "SELECT * FROM gb_wins WHERE user_id = ?"
    if not include_system:
        sql += " AND source != 'system'"
    return _all(sql + " ORDER BY id DESC LIMIT ?", (user_id, limit))


def win_count(user_id: int, include_system: bool = False) -> int:
    sql = "SELECT COUNT(*) AS c FROM gb_wins WHERE user_id = ?"
    if not include_system:
        sql += " AND source != 'system'"
    row = _one(sql, (user_id,))
    return int(row["c"]) if row else 0


def mark_win_shown(win_id: int) -> None:
    _exec("UPDATE gb_wins SET shown_count = shown_count + 1, last_shown = ? WHERE id = ?", (_now(), win_id))


def search_wins(user_id: int, fts_query: str, limit: int = 10) -> list[sqlite3.Row]:
    """bm25-ranked FTS5 match; a malformed query is retried as a quoted phrase."""
    sql = ("SELECT w.*, bm25(gb_wins_fts) AS rank FROM gb_wins_fts JOIN gb_wins w ON w.id = gb_wins_fts.rowid "
           "WHERE gb_wins_fts MATCH ? AND w.user_id = ? AND w.source != 'system' ORDER BY rank LIMIT ?")
    conn = _connect()
    try:
        try:
            return conn.execute(sql, (fts_query, user_id, limit)).fetchall()
        except sqlite3.OperationalError:
            quoted = '"' + fts_query.replace('"', "") + '"'
            return conn.execute(sql, (quoted, user_id, limit)).fetchall()
    finally:
        conn.close()


# ── rest ──────────────────────────────────────────────────────────────────

def add_rest_log(**cols: Any) -> int:
    return _insert("gb_rest_logs", **cols)


def update_rest_log(rest_id: int, **fields: Any) -> None:
    _update("gb_rest_logs", "id = ?", (rest_id,), **fields)


def rest_logs_for_day(user_id: int, day: str) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_rest_logs WHERE user_id = ? AND day = ? ORDER BY id", (user_id, day))


# ── timers ────────────────────────────────────────────────────────────────

def create_timer(**cols: Any) -> int:
    cols.setdefault("started_at", _now())
    return _insert("gb_timers", **cols)


def get_timer(timer_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_timers WHERE id = ?", (timer_id,))


def update_timer(timer_id: int, **fields: Any) -> None:
    _update("gb_timers", "id = ?", (timer_id,), **fields)


def due_timers(now_iso: str) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_timers WHERE status = 'running' AND due_at <= ? ORDER BY due_at", (now_iso,))


def running_timers(user_id: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_timers WHERE user_id = ? AND status = 'running' ORDER BY id DESC", (user_id,))


def running_timer_for_touch(touch_id: int) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_timers WHERE touch_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1",
                (touch_id,))


# ── levels / snapshots / activity / exposures ─────────────────────────────

def levels(user_id: int) -> dict[str, int]:
    out = {"A": 1, "B": 1, "C": 1, "tier": 1}
    for r in _all("SELECT track, MAX(level) AS lv FROM gb_levels WHERE user_id = ? GROUP BY track", (user_id,)):
        out[r["track"]] = max(out.get(r["track"], 1), int(r["lv"]))
    return out


def set_level(user_id: int, track: str, level: int, evidence: dict | None = None) -> None:
    _exec("INSERT OR IGNORE INTO gb_levels (user_id, track, level, reached_at, evidence) VALUES (?, ?, ?, ?, ?)",
          (user_id, track, level, _now(), _j(evidence or {})))


def level_rows(user_id: int) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_levels WHERE user_id = ? ORDER BY reached_at", (user_id,))


def snapshot_get(user_id: int, name: str) -> float | None:
    row = _one("SELECT value FROM gb_snapshots WHERE user_id = ? AND name = ?", (user_id, name))
    return None if row is None or row["value"] is None else float(row["value"])


def snapshot_set(user_id: int, name: str, value: float | None) -> None:
    _exec("INSERT INTO gb_snapshots (user_id, name, value, taken_at) VALUES (?, ?, ?, ?) "
          "ON CONFLICT(user_id, name) DO UPDATE SET value = excluded.value, taken_at = excluded.taken_at",
          (user_id, name, value, _now()))


def touch_activity(user_id: int, day: str, done: bool = False) -> None:
    now = _now()
    _exec("INSERT INTO gb_activity (user_id, day, touches_done, first_at, last_at) VALUES (?, ?, ?, ?, ?) "
          "ON CONFLICT(user_id, day) DO UPDATE SET touches_done = touches_done + excluded.touches_done, "
          "last_at = excluded.last_at", (user_id, day, 1 if done else 0, now, now))


def activity_days(user_id: int) -> list[str]:
    return [r["day"] for r in _all("SELECT day FROM gb_activity WHERE user_id = ? ORDER BY day", (user_id,))]


def exposure(user_id: int, module_id: str) -> sqlite3.Row | None:
    return _one("SELECT * FROM gb_module_exposures WHERE user_id = ? AND module_id = ?", (user_id, module_id))


def bump_exposure(user_id: int, module_id: str, day: str) -> int:
    _exec("INSERT INTO gb_module_exposures (user_id, module_id, exposures, last_day) VALUES (?, ?, 1, ?) "
          "ON CONFLICT(user_id, module_id) DO UPDATE SET exposures = exposures + 1, last_day = excluded.last_day",
          (user_id, module_id, day))
    row = exposure(user_id, module_id)
    return int(row["exposures"]) if row else 1


def add_feedback(user_id: int, module_id: str | None, entry_id: int | None, vote: int | None, text: str | None) -> int:
    return _insert("gb_feedback", user_id=user_id, module_id=module_id, entry_id=entry_id, vote=vote, text=text,
                   created_at=_now())


# ── visualizations ────────────────────────────────────────────────────────

def add_visualization(user_id: int, day: str, roles: list[str], horizon: str | None, text: str,
                      tags: list[str] | None = None) -> int:
    return _insert("gb_visualizations", user_id=user_id, day=day, roles=_j(roles), horizon=horizon, text=text,
                   tags=_j(tags or []), created_at=_now())


def update_visualization(vis_id: int, **fields: Any) -> None:
    if "tags" in fields and not isinstance(fields["tags"], str):
        fields["tags"] = _j(fields["tags"])
    _update("gb_visualizations", "id = ?", (vis_id,), **fields)


def visualizations(user_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return _all("SELECT * FROM gb_visualizations WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))


def recent_visualization_roles(user_id: int, days: int, today: str) -> list[list[str]]:
    since = clock.day_offset(today, -days)
    return [loads(r["roles"], []) for r in
            _all("SELECT roles FROM gb_visualizations WHERE user_id = ? AND day >= ?", (user_id, since))]


# ── export / delete ───────────────────────────────────────────────────────

def export_user(user_id: int) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    conn = _connect()
    try:
        for table in USER_TABLES:
            rows = conn.execute(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,)).fetchall()
            out[table] = [dict(r) for r in rows]
    finally:
        conn.close()
    return out


def delete_user(user_id: int) -> None:
    conn = _connect()
    try:
        win_ids = [r["id"] for r in conn.execute("SELECT id FROM gb_wins WHERE user_id = ?", (user_id,))]
        for wid in win_ids:
            conn.execute("DELETE FROM gb_wins_fts WHERE rowid = ?", (wid,))
        for table in USER_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def wal_checkpoint() -> None:
    conn = _connect()
    try:
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        conn.close()
