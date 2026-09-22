"""Success Bank: entries, tagging, weighted reminders, FTS search."""
from __future__ import annotations

import random
import re

from . import clock, gdb
from .render import esc

TAGS = ["social_courage", "tried_new_thing", "looked_foolish_anyway", "finished_hard_thing", "willpower_win",
        "kept_promise_to_self", "helped_someone", "stayed_calm", "learned", "chose_right_thing",
        "rested_on_purpose", "other"]
DOMAINS = ["work", "family", "health", "creative", "admin", "social", "other"]

_DATE_RE = re.compile(r"^\s*((?:19|20)\d\d(?:-\d\d(?:-\d\d)?)?)\s*[:,\-–]?\s*(.*)$", re.S)


def split_dated(text: str) -> tuple[str | None, str]:
    m = _DATE_RE.match(text)
    if m and m.group(2).strip():
        return m.group(1), m.group(2).strip()
    return None, text.strip()


def add(user_id: int, text: str, source: str, happened_on: str | None) -> int:
    win_id = gdb.add_win(user_id, text.strip(), source, happened_on)
    try:
        from . import gai
        tagged = gai.tag_win(text)
        if tagged:
            gdb.update_win(win_id, tags=tagged.get("tags") or ["other"], difficulty=tagged.get("difficulty"),
                           domain=tagged.get("domain"))
    except Exception as e:
        print(f"  growth win tag error: {e}")
    return win_id


def add_system(user_id: int, text: str, day: str) -> int:
    return gdb.add_win(user_id, text, "system", day, tags=["system"], difficulty=None, domain=None)


def remind(user_id: int, tag: str | None = None) -> "gdb.sqlite3.Row | None":
    """One win, weighted toward difficulty and toward entries not shown recently."""
    rows = gdb.wins(user_id, limit=500)
    if tag:
        tagged = [r for r in rows if tag in (gdb.loads(r["tags"], []) or [])]
        rows = tagged or rows
    if not rows:
        return None
    now = clock.now()
    weights = []
    for r in rows:
        w = 1.0 + 0.5 * float(r["difficulty"] or 2)
        if r["last_shown"]:
            days = (now - clock.parse(r["last_shown"])).days
            w *= min(1.0, 0.15 + days / 14.0)
        w /= 1.0 + 0.2 * float(r["shown_count"] or 0)
        weights.append(w)
    row = random.choices(rows, weights=weights, k=1)[0]
    gdb.mark_win_shown(row["id"])
    return row


def format_line(row) -> str:
    when = row["happened_on"] or (row["created_at"] or "")[:10]
    return f"{when}: {row['text']}"


def format_html(row) -> str:
    return f"<i>{esc(row['happened_on'] or (row['created_at'] or '')[:10])}</i> {esc(row['text'])}"


_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def fts_query_from(text: str) -> str:
    words = [w for w in _WORD_RE.findall(text.lower()) if len(w) > 2 and w not in _STOP]
    return " OR ".join(words) if words else '""'


_STOP = {"the", "and", "did", "when", "have", "what", "that", "with", "this", "for", "you", "was", "were",
         "how", "who", "did", "i", "like", "something", "ever", "time", "times"}


def search(user_id: int, query: str, limit: int = 5) -> list:
    rows = gdb.search_wins(user_id, fts_query_from(query), limit=limit * 3)
    try:
        from . import gai
        order = gai.rerank_wins(query, [{"id": r["id"], "text": r["text"], "tags": gdb.loads(r["tags"], [])}
                                        for r in rows])
        if order:
            by_id = {r["id"]: r for r in rows}
            rows = [by_id[i] for i in order if i in by_id] + [r for r in rows if r["id"] not in order]
    except Exception as e:
        print(f"  growth rerank error: {e}")
    return list(rows)[:limit]
