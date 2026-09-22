"""Side effects keyed by the `effect:` name on a YAML step. Each gets the
scope (user, day, touch, state, answers, ctx, core), the step, and the value."""
from __future__ import annotations

import re
from typing import Any, Callable

from . import bank, clock, excuses, gdb, priority
from .ctx import rest_menu

Effect = Callable[[dict, dict, Any], None]


def run(name: str, sc: dict, step: dict, value: Any) -> None:
    fn = EFFECTS.get(name)
    if fn is None:
        print(f"  growth: unknown effect {name!r}")
        return
    try:
        fn(sc, step, value)
    except Exception as e:  # an effect must never kill the card
        print(f"  growth effect {name} error: {e}")


def _base(step: dict) -> str:
    return step.get("base_field") or step.get("field") or step["id"]


def _lines(text: Any) -> list[str]:
    if isinstance(text, list):
        return [str(x).strip() for x in text if str(x).strip()]
    return [ln.strip(" •-*\t") for ln in str(text or "").replace(";", "\n").replace(",", "\n").splitlines()
            if ln.strip(" •-*\t")]


# ── day / core ────────────────────────────────────────────────────────────

def day_field(sc, step, value) -> None:
    field = _base(step)
    uid, day = sc["user"]["user_id"], sc["day"]
    if field == "wont":
        if value in ("none", None):
            gdb.update_day(uid, day, wont=None)
            return
        items = rest_menu(sc["core"])["feels_like"]
        try:
            value = str(items[int(value)])
        except (ValueError, IndexError, TypeError):
            value = str(value)
    if field not in ("capacity", "sleep", "failure_point", "wont", "wont_kept", "easier_tomorrow"):
        return
    gdb.update_day(uid, day, **{field: value})
    if field == "capacity":
        gdb.update_day(uid, day, planned_at=gdb.get_day(uid, day)["planned_at"] or clock.iso(clock.now()))


def rank_ok(sc, step, value) -> None:
    uid, day = sc["user"]["user_id"], sc["day"]
    if value == "swap12":
        priority.swap(uid, day, 1, 2)
    elif value == "swap23":
        priority.swap(uid, day, 2, 3)


def manual_ranks(sc, step, value) -> None:
    raw = str(value or "")
    if "\n" in raw or ";" in raw:
        parts = raw.replace(";", "\n").splitlines()
    elif raw.count(",") >= 2:
        parts = raw.split(",")
    else:
        parts = [raw]
    titles = [ln for ln in parts if ln.strip()]
    titles = [re.sub(r"^\s*(P\d\s*[:.\-·]?\s*|\d+[.)]\s*)", "", t).strip() for t in titles]
    priority.set_manual(sc["user"]["user_id"], sc["day"], [t for t in titles if t][:5])


def set_tier(sc, step, value) -> None:
    gdb.update_day(sc["user"]["user_id"], sc["day"], tier=str(value))


def core_field(sc, step, value) -> None:
    _store_core(sc, _base(step), value)


def core_field_next(sc, step, value) -> None:
    field = sc["ctx"].get("next_core_field")
    if field:
        _store_core(sc, field, value)


def rest_menu_field(sc, step, value) -> None:
    _store_core(sc, _base(step), value)


def _store_core(sc, field: str, value: Any) -> None:
    uid = sc["user"]["user_id"]
    core = gdb.get_core(uid)
    if field in ("rest_restores", "rest_feels_like"):
        rm = rest_menu(core)
        if field == "rest_restores":
            items = []
            for ln in str(value or "").splitlines():
                ln = ln.strip(" •-*\t")
                if not ln:
                    continue
                m = re.match(r"^(.*?)[,\s]+(\d+)\s*(?:min|m|minutes)?\s*$", ln)
                if m:
                    items.append({"activity": m.group(1).strip(" ,"), "minutes": int(m.group(2))})
                else:
                    items.append({"activity": ln, "minutes": 20})
            rm["restores"] = items
        else:
            rm["feels_like"] = _lines(value)
        gdb.set_core(uid, "rest_menu", rm)
        return
    if field == "signature_strengths":
        from . import loader
        names = loader.content().copy["strengths"]
        if isinstance(value, list):
            value = [names[int(i)] if str(i).isdigit() and int(i) < len(names) else str(i) for i in value]
        else:
            value = _lines(value)
    elif field == "franklin_virtues":
        value = [str(v) for v in value] if isinstance(value, list) else _lines(value)
    elif field in ("fixed_areas", "avoided_tasks", "roles"):
        value = _lines(value)
    gdb.set_core(uid, field, value)


# ── challenges ────────────────────────────────────────────────────────────

def _task_from_pick(sc, value) -> tuple[int | None, str | None, int | None]:
    if value in ("none", None):
        return None, None, None
    if value == "other":
        return None, "Other", None
    plist = priority.day_list(sc["user"]["user_id"], sc["day"])
    for p in plist:
        if f"p{p['p']}" == str(value):
            return p.get("task_id"), p["title"], p["p"]
    return None, str(value), None


def plan_challenge(sc, step, value) -> None:
    if value in ("none", None):
        return
    n = 2 if _base(step).endswith("2") else 1
    task_id, title, p = _task_from_pick(sc, value)
    uid, day = sc["user"]["user_id"], sc["day"]
    existing = [c for c in gdb.challenges_for_day(uid, day) if c["n"] == n]
    day_row = gdb.get_day(uid, day)
    tier = (day_row["tier"] if day_row and day_row["tier"] else sc["user"]["tier"]) or "t30"
    if existing:
        gdb.update_challenge(existing[0]["id"], task_id=task_id, task_title=title, p=p, tier=tier)
        cid = existing[0]["id"]
    else:
        cid = gdb.create_challenge(uid, day, n, tier, task_id, title, p)
    sc["state"].setdefault("meta", {})[f"challenge{n}"] = cid


def challenge_start(sc, step, value) -> None:
    if value in ("none", None):
        return
    uid, day = sc["user"]["user_id"], sc["day"]
    task_id, title, p = _task_from_pick(sc, value)
    todays = gdb.challenges_for_day(uid, day)
    planned = [c for c in todays if c["status"] == "planned" and
               ((task_id and c["task_id"] == task_id) or (title and c["task_title"] == title))]
    if not planned:
        planned = [c for c in todays if c["status"] == "planned"]
    day_row = gdb.get_day(uid, day)
    tier = (day_row["tier"] if day_row and day_row["tier"] else sc["user"]["tier"]) or "t30"
    if planned:
        cid = planned[0]["id"]
        gdb.update_challenge(cid, task_id=task_id, task_title=title, p=p)
    else:
        n = max([c["n"] for c in todays] + [0]) + 1
        cid = gdb.create_challenge(uid, day, n, tier, task_id, title, p)
    gdb.update_challenge(cid, status="started", started_at=clock.iso(clock.now()))
    gdb.update_touch(sc["touch"]["id"], challenge_id=cid)
    sc["touch"] = gdb.get_touch(sc["touch"]["id"])
    sc["state"].setdefault("meta", {})["challenge_id"] = cid


def challenge_field(sc, step, value) -> None:
    cid = sc["touch"]["challenge_id"]
    if not cid or value is None:
        return
    field = _base(step)
    if field in ("done_looks_like", "why_not_p5", "est_min", "moved", "resume_note", "actual_min", "next_step",
                 "premortem", "error_log"):
        gdb.update_challenge(cid, **{field: value})


def challenge_end(sc, step, value) -> None:
    cid = sc["touch"]["challenge_id"]
    if not cid:
        return
    ch = gdb.get_challenge(cid)
    a = sc["answers"]
    gdb.update_challenge(cid, status="ended", ended_at=clock.iso(clock.now()), next_step=None
                         if value != "step" else ch["next_step"])
    ch = gdb.get_challenge(cid)
    meta = {"tier": ch["tier"], "moved": ch["moved"], "est_min": ch["est_min"], "actual_min": ch["actual_min"],
            "resume_note": "present" if (ch["resume_note"] or "").strip() else "absent", "challenge_id": cid,
            "task_title": ch["task_title"]}
    gdb.emit(sc["user"]["user_id"], sc["day"], "challenge_ended", meta)
    if a.get("next_choice") == "rest":
        sc["state"].setdefault("meta", {})["offer_rest"] = True


def challenge_outcome(sc, step, value) -> None:
    item = step.get("item") or {}
    n = item.get("n")
    for ch in gdb.challenges_for_day(sc["user"]["user_id"], sc["day"]):
        if ch["n"] == n:
            gdb.update_challenge(ch["id"], outcome=value)
            if ch["status"] == "started" and value in ("done", "partial"):
                gdb.update_challenge(ch["id"], status="ended", ended_at=clock.iso(clock.now()))


# ── refocus / excuses ─────────────────────────────────────────────────────

def refocus_number(sc, step, value) -> None:
    uid, day = sc["user"]["user_id"], sc["day"]
    v = str(value)
    on_kind = "p" if v.startswith("p") else ("rest" if v == "rest" else "other")
    on_p = int(v[1:]) if on_kind == "p" and v[1:].isdigit() else None
    plist = priority.day_list(uid, day)
    p1 = next((p for p in plist if p["p"] == 1), None)
    prev = gdb.last_state_event_ts(uid, day)
    mins = None
    if prev:
        mins = int((clock.now() - clock.parse(prev)).total_seconds() // 60)
    log_id = gdb.add_refocus_log(user_id=uid, day=day, touch_id=sc["touch"]["id"], on_kind=on_kind, on_p=on_p,
                                 p1_task_id=p1.get("task_id") if p1 else None,
                                 p1_title=p1["title"] if p1 else None, minutes_since_prev=mins)
    sc["state"].setdefault("meta", {})["refocus_log_id"] = log_id


def refocus_rest_kind(sc, step, value) -> None:
    log_id = sc["state"].get("meta", {}).get("refocus_log_id")
    if log_id:
        gdb._update("gb_refocus_logs", "id = ?", (log_id,), rest_mode=value)


def _excuse_task(sc) -> tuple[int | None, str | None]:
    meta = sc["state"].get("meta", {})
    if meta.get("avoid_task"):
        t = meta["avoid_task"]
        return t.get("task_id"), t.get("title")
    plist = priority.day_list(sc["user"]["user_id"], sc["day"])
    p1 = next((p for p in plist if p["p"] == 1), None)
    return (p1.get("task_id"), p1["title"]) if p1 else (None, None)


def excuse_log(sc, step, value) -> None:
    if value in ("new", "note_only", None):
        if value == "note_only":
            family = 2 if sc["touch"]["kind"] == "refocus" else 1
            gdb.emit(sc["user"]["user_id"], sc["day"], "excuse_noted", {"family": family})
        return
    _log_excuse(sc, int(value))


def excuse_new(sc, step, value) -> None:
    text = str(value or "").strip()
    if not text:
        return
    family = 2 if sc["touch"]["kind"] == "refocus" else 1
    ex_id = gdb.add_excuse(sc["user"]["user_id"], family, text[:120], size="small",
                           counter_move="5-min start on it.", follow_up="start5")
    sc["state"].setdefault("meta", {})["new_excuse_id"] = ex_id
    sc["answers"]["excuse_id"] = ex_id
    _log_excuse(sc, ex_id)


def _log_excuse(sc, ex_id: int) -> None:
    uid, day = sc["user"]["user_id"], sc["day"]
    ex = gdb.get_excuse(ex_id)
    if ex is None:
        return
    task_id, task_name = _excuse_task(sc)
    log_id = gdb.add_excuse_log(uid, day, ex_id, sc["touch"]["id"], task_id, task_name)
    meta = sc["state"].setdefault("meta", {})
    meta["excuse_log_id"] = log_id
    rlog = meta.get("refocus_log_id")
    if rlog:
        flag = 1 if ex["flag"] == "interest_drift" else 0
        gdb._update("gb_refocus_logs", "id = ?", (rlog,), excuse_log_id=log_id, interest_flag=flag)
    gdb.emit(uid, day, "excuse_logged", {"family": ex["family"], "excuse_id": ex_id, "size": ex["size"],
                                        "task_name": task_name, "flag": ex["flag"]})


def avoid_task(sc, step, value) -> None:
    task_id, title, p = _task_from_pick(sc, value)
    sc["state"].setdefault("meta", {})["avoid_task"] = {"task_id": task_id, "title": title, "p": p}


# ── rest ──────────────────────────────────────────────────────────────────

def rest_budget(sc, step, value) -> None:
    items = rest_menu(sc["core"])["restores"]
    chosen = []
    for v in (value or []):
        try:
            chosen.append(items[int(v)])
        except (ValueError, IndexError, TypeError):
            continue
    gdb.update_day(sc["user"]["user_id"], sc["day"], rest_budget=gdb._j(chosen))


def rest_pick(sc, step, value) -> None:
    uid, day = sc["user"]["user_id"], sc["day"]
    d = gdb.get_day(uid, day)
    items = gdb.loads(d["rest_budget"], []) if d else []
    if value == "other15":
        item, minutes = "Rest", 15
    else:
        try:
            it = items[int(value)]
            item, minutes = it.get("activity", "Rest"), int(it.get("minutes") or 15)
        except (ValueError, IndexError, TypeError):
            item, minutes = "Rest", 15
    meta = sc["state"].setdefault("meta", {})
    meta.update({"rest_item": item, "rest_minutes": minutes, "rest_seconds": minutes * 60})
    rid = gdb.add_rest_log(user_id=uid, day=day, item=item, budget_min=minutes,
                           started_at=clock.iso(clock.now()), mode="budgeted")
    meta["rest_log_id"] = rid
    gdb.emit(uid, day, "rest_started", {"item": item, "minutes": minutes})


def rest_end(sc, step, value) -> None:
    meta = sc["state"].get("meta", {})
    rid = meta.get("rest_log_id")
    if not rid:
        return
    row = gdb._one("SELECT * FROM gb_rest_logs WHERE id = ?", (rid,))
    started = clock.parse(row["started_at"]) if row and row["started_at"] else clock.now()
    actual = max(1, int((clock.now() - started).total_seconds() // 60))
    gdb.update_rest_log(rid, ended_at=clock.iso(clock.now()), actual_min=actual)


def rest_restored(sc, step, value) -> None:
    item = (step.get("item") or {}).get("title", "")
    name = item.split(",")[0].strip()
    for r in gdb.rest_logs_for_day(sc["user"]["user_id"], sc["day"]):
        if r["item"] == name and r["restored"] is None:
            gdb.update_rest_log(r["id"], restored=value)
            return
    gdb.add_rest_log(user_id=sc["user"]["user_id"], day=sc["day"], item=name or "rest", mode="budgeted",
                     restored=value)


# ── wins / entries / visualization ────────────────────────────────────────

def proud_win(sc, step, value) -> None:
    if value:
        bank.add(sc["user"]["user_id"], str(value), "evening", sc["day"])


def win_save(sc, step, value) -> None:
    if value:
        bank.add(sc["user"]["user_id"], str(value), "win", sc["day"])


def win_old_save(sc, step, value) -> None:
    if value:
        happened, text = bank.split_dated(str(value))
        bank.add(sc["user"]["user_id"], text, "win_old", happened)


def entry(sc, step, value) -> None:
    return None  # gb_entries row is written by the session; nothing extra


def visualization_offer(sc, step, value) -> None:
    if value == "later":
        gdb.emit(sc["user"]["user_id"], sc["day"], "visualization_later", {})


def visualization_save(sc, step, value) -> None:
    from . import visualization
    if value:
        visualization.save(sc["user"], sc["day"], sc["state"], str(value))


EFFECTS: dict[str, Effect] = {
    "day_field": day_field, "rank_ok": rank_ok, "manual_ranks": manual_ranks, "set_tier": set_tier,
    "core_field": core_field, "core_field_next": core_field_next, "rest_menu_field": rest_menu_field,
    "plan_challenge": plan_challenge, "challenge_start": challenge_start, "challenge_field": challenge_field,
    "challenge_end": challenge_end, "challenge_outcome": challenge_outcome,
    "refocus_number": refocus_number, "refocus_rest_kind": refocus_rest_kind,
    "excuse_log": excuse_log, "excuse_new": excuse_new, "avoid_task": avoid_task,
    "rest_budget": rest_budget, "rest_pick": rest_pick, "rest_end": rest_end, "rest_restored": rest_restored,
    "proud_win": proud_win, "win_save": win_save, "win_old_save": win_old_save, "entry": entry,
    "visualization_offer": visualization_offer, "visualization_save": visualization_save,
}
