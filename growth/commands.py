"""Growth commands (spec §9). Dispatched from stm_bot.handle_command when
`cmd in COMMANDS`; `/now` without an argument and `/start` are routed here by
stm_bot explicitly."""
from __future__ import annotations

import json

import telegram_api as tg

from . import bank, clock, excuses, gdb, levels, loader, measures, priority, render, session, settings
from .render import esc

COMMANDS = {
    "start", "why", "status", "morning", "capacity", "predict", "aim", "end", "now", "rest", "avoid",
    "excuses", "misalloc", "start5", "surf", "evening", "good", "win", "win_old", "wins", "remind", "flash",
    "dispute", "feedback", "survival", "stats", "skip", "mydata", "observe", "visualize", "tier",
}

# Registered in Telegram's "/" menu (the rest work as precision commands).
MENU_COMMANDS = [
    ("morning", "Morning: capacity, today's P1–P3, challenges, rest budget"),
    ("aim", "Challenge start: aim, why this, estimate"),
    ("end", "Challenge end: moved?, resume note, actual time"),
    ("now", "Refocus: what number are you on? (/now 30 → what fits in 30 min)"),
    ("rest", "Budgeted rest with a timer"),
    ("avoid", "Log an excuse for not starting → counter-move → 5-min start"),
    ("evening", "Evening: done/rolled, rest, one proud-of line"),
    ("win", "Keep a win in the Success Bank"),
    ("wins", "Search the Success Bank, e.g. /wins when did I talk to people"),
    ("remind", "One past win"),
    ("status", "Track levels, streak, today's refocus times"),
    ("skip", "Pass the open text question"),
    ("survival", "/survival on|off — collapse the arc, freeze levels"),
    ("why", "View or edit core data"),
    ("observe", "This week's observation lens"),
]

STAGE2 = {"flash": "Flashback Log", "dispute": "ABCDE", "good": "Three Good Things", "feedback": "Feedback",
          "stats": "Stats"}


def needs_onboarding(msg: dict) -> bool:
    """True when /start should run setup for this sender."""
    user_id = (msg.get("from") or {}).get("id")
    if user_id not in settings.ALLOWED_USER_IDS:
        return False
    user = gdb.get_user(user_id)
    return user is None or user["onboarding_step"] != "done"


def help_block(full: bool = False) -> str:
    c = loader.content().copy
    return (c["help"] if full else c["help_short"]).strip()


def _user_for(msg: dict):
    frm = msg.get("from") or {}
    user_id = frm.get("id")
    if user_id not in settings.ALLOWED_USER_IDS:
        return None
    chat_id = (msg.get("chat") or {}).get("id") or settings.CHAT_ID
    thread_id = msg.get("message_thread_id") or settings.THREAD_ID
    if settings.THREAD_ID and settings.CHAT_ID:
        chat_id, thread_id = settings.CHAT_ID, settings.THREAD_ID
    user = gdb.ensure_user(user_id, chat_id, thread_id, settings.TZ_NAME)
    excuses.seed_user(user_id)
    return user


def _say(user, text: str) -> None:
    render.plain(user["chat_id"], user["thread_id"], text)


def handle_command(cmd: str, args: list[str], msg: dict, thread_id: int | None) -> None:
    user = _user_for(msg)
    if user is None:
        return
    fn = _HANDLERS.get(cmd)
    if fn is None:
        return
    try:
        fn(user, args, msg)
    except Exception as e:
        print(f"  growth /{cmd} error: {e}")
        _say(user, f"/{cmd} failed: {esc(e)}")


# ── setup / info ──────────────────────────────────────────────────────────

def cmd_start(user, args, msg) -> None:
    if user["onboarding_step"] != "done":
        gdb.update_user(user["user_id"], onboarding_step="started")
        session.start(user, "onboard")
        gdb.update_user(user["user_id"], onboarding_step="done")
        return
    _say(user, help_block(full=True))


CORE_EDITABLE = ["why", "eulogy", "fixed_areas", "top_interest", "signature_strengths", "avoided_tasks",
                 "franklin_virtues", "roles", "rest_restores", "rest_feels_like"]


def cmd_why(user, args, msg) -> None:
    from . import effects
    if args and args[0] in CORE_EDITABLE:
        field = args[0]
        value = " ".join(args[1:]).strip()
        if not value:
            _say(user, f"Usage: /why {field} &lt;text&gt;")
            return
        sc = {"user": user, "day": clock.local_today(settings.TZ), "core": gdb.get_core(user["user_id"])}
        effects._store_core(sc, field, value.replace(" / ", "\n"))
        gdb.emit(user["user_id"], sc["day"], "core_field_set", {"field": field})
        _say(user, f"{esc(field)} updated.")
        return
    core = gdb.get_core(user["user_id"])
    if not core:
        _say(user, "No core data yet. /start runs setup.")
        return
    lines = ["<b>Core data</b>"]
    for k, v in core.items():
        if isinstance(v, dict):
            v = "; ".join(f"{kk}: {vv}" for kk, vv in v.items())
        elif isinstance(v, list):
            v = ", ".join(str(x) if not isinstance(x, dict) else f"{x.get('activity')} {x.get('minutes')}m" for x in v)
        lines.append(f"<b>{esc(k)}</b>: {esc(v)}")
    lines.append("<i>Edit: /why &lt;field&gt; &lt;text&gt; (fields: " + ", ".join(CORE_EDITABLE) + ")</i>")
    _say(user, "\n".join(lines))


def cmd_status(user, args, msg) -> None:
    today = clock.local_today(settings.TZ)
    text = levels.status_text(user, today)
    touches = [t for t in gdb.touches_for_day(user["user_id"], today) if t["kind"] == "refocus" and t["due_at"]]
    if touches:
        text += "\nRefocus today: " + ", ".join(
            f"{clock.hhmm_local(t['due_at'], settings.TZ)} ({t['status']})" for t in touches)
    ch = gdb.open_challenge(user["user_id"])
    if ch:
        text += f"\nOpen challenge: {ch['task_title']} (/end)."
    _say(user, esc(text))


def cmd_observe(user, args, msg) -> None:
    from . import ctx as ctxmod
    today = clock.local_today(settings.TZ)
    if args and args[0] in ("restart", "start"):
        gdb.update_user(user["user_id"], obs_started_on=today)
        _say(user, "Observation cycle restarted at week 1.")
        return
    if len(args) == 2 and args[0] == "week" and args[1].isdigit():
        n = int(args[1])
        started = clock.day_offset(today, -(n - 1) * 7)
        gdb.update_user(user["user_id"], obs_started_on=started)
        _say(user, f"Observation week set to {n}.")
        return
    user = gdb.get_user(user["user_id"])
    sc = {"user": user, "day": today, "core": gdb.get_core(user["user_id"]), "state": {}, "answers": {}, "ctx": {}}
    _say(user, esc(ctxmod.text_from("observation_intro", sc)) + "\n<i>/observe week N · /observe restart</i>")


def cmd_survival(user, args, msg) -> None:
    c = loader.content().copy
    if args and args[0].lower() in ("on", "off"):
        on = args[0].lower() == "on"
        gdb.update_user(user["user_id"], survival=1 if on else 0)
        gdb.emit(user["user_id"], clock.local_today(settings.TZ), "survival_on" if on else "survival_off", {})
        _say(user, c["survival_on"] if on else c["survival_off"])
        return
    _say(user, f"Survival mode is {'on' if user['survival'] else 'off'}. /survival on|off")


def cmd_tier(user, args, msg) -> None:
    unlocked = levels.unlocked_tiers(user["user_id"])
    if args and args[0] in {t["id"] for t in unlocked}:
        gdb.update_user(user["user_id"], tier=args[0])
        gdb.update_day(user["user_id"], clock.local_today(settings.TZ), tier=args[0])
        _say(user, f"Block size: {args[0]}.")
        return
    _say(user, "Unlocked: " + ", ".join(f"{t['id']} ({t['label']})" for t in unlocked))


# ── the arc ───────────────────────────────────────────────────────────────

def cmd_morning(user, args, msg) -> None:
    session.fire_scheduled_now(user, "morning")


def cmd_evening(user, args, msg) -> None:
    session.fire_scheduled_now(user, "evening")


def cmd_now(user, args, msg) -> None:
    session.start(user, "refocus")


def cmd_aim(user, args, msg) -> None:
    if gdb.open_challenge(user["user_id"]):
        _say(user, "A challenge is open. /end it first.")
        return
    session.start(user, "aim")


def cmd_end(user, args, msg) -> None:
    ch = gdb.open_challenge(user["user_id"])
    if ch is None:
        _say(user, "No challenge open. /aim starts one.")
        return
    session.start(user, "end", challenge_id=ch["id"])


def cmd_rest(user, args, msg) -> None:
    meta = {}
    if args and args[0].isdigit():
        minutes = max(1, min(180, int(args[0])))
        today = clock.local_today(settings.TZ)
        rid = gdb.add_rest_log(user_id=user["user_id"], day=today, item="Rest", budget_min=minutes,
                               started_at=clock.iso(clock.now()), mode="chosen")
        meta = {"rest_item": "Rest", "rest_minutes": minutes, "rest_seconds": minutes * 60, "rest_log_id": rid,
                "skip_steps": ["item"]}
    session.start(user, "rest", meta=meta)


def cmd_avoid(user, args, msg) -> None:
    session.start(user, "avoid")


def cmd_surf(user, args, msg) -> None:
    session.start(user, "surf")


def cmd_start5(user, args, msg) -> None:
    session.start(user, "start5")


def cmd_capacity(user, args, msg) -> None:
    _say(user, "Capacity and sleep are the first two taps of /morning.")


def cmd_predict(user, args, msg) -> None:
    text = " ".join(args).strip()
    today = clock.local_today(settings.TZ)
    if not text:
        _say(user, "Usage: /predict &lt;today's failure point&gt;")
        return
    gdb.update_day(user["user_id"], today, failure_point=text)
    gdb.emit(user["user_id"], today, "failure_point_set", {"value": text})
    _say(user, "Noted.")


# ── Success Bank ──────────────────────────────────────────────────────────

def cmd_win(user, args, msg) -> None:
    text = " ".join(args).strip()
    if text:
        bank.add(user["user_id"], text, "win", clock.local_today(settings.TZ))
        gdb.emit(user["user_id"], clock.local_today(settings.TZ), "win_logged", {"source": "win"})
        _say(user, "Kept.")
        return
    session.start(user, "win")


def cmd_win_old(user, args, msg) -> None:
    text = " ".join(args).strip()
    if text:
        happened, body = bank.split_dated(text)
        bank.add(user["user_id"], body, "win_old", happened)
        _say(user, f"Kept ({gdb.win_count(user['user_id'])} wins stored).")
        return
    session.start(user, "win_old")


def cmd_wins(user, args, msg) -> None:
    query = " ".join(args).strip()
    if not query:
        rows = gdb.wins(user["user_id"], limit=5)
        _say(user, "<b>Latest wins</b>\n" + ("\n".join(bank.format_html(r) for r in rows) or "None yet.")
             + "\n<i>/wins &lt;question&gt; searches.</i>")
        return
    rows = bank.search(user["user_id"], query, limit=5)
    if not rows:
        _say(user, "Nothing matched. Try other words.")
        return
    _say(user, "\n".join(bank.format_html(r) for r in rows))


def cmd_remind(user, args, msg) -> None:
    row = bank.remind(user["user_id"], args[0] if args else None)
    _say(user, bank.format_html(row) if row else "No wins stored yet. /win_old adds one.")


# ── reports / data ────────────────────────────────────────────────────────

def cmd_excuses(user, args, msg) -> None:
    today = clock.local_today(settings.TZ)
    text = esc(excuses.report(user["user_id"], today))
    rows = gdb.all_excuses(user["user_id"])
    text += "\n\n<b>Catalog</b>\n" + "\n".join(f"{r['id']}. [{r['family']}/{r['size']}] {esc(r['text'])}" for r in rows)
    _say(user, text)


def cmd_misalloc(user, args, msg) -> None:
    today = clock.local_today(settings.TZ)
    days = [clock.day_offset(today, -i) for i in range(14)]
    mean, n = measures.misalloc_minutes_per_day(user["user_id"], days)
    logs = gdb.refocus_logs(user["user_id"], [today])
    lines = [f"Misallocation (sampled proxy), last 14 days: "
             + (f"{round(mean)} min/day over {n} measured days." if mean is not None else "no refocus data yet.")]
    if logs:
        lines.append("Today: " + ", ".join(
            f"{clock.hhmm_local(r['answered_at'], settings.TZ)} {r['on_kind'] if r['on_kind'] != 'p' else 'P' + str(r['on_p'])}"
            + (f" ({r['rest_mode']})" if r["rest_mode"] else "") for r in logs))
    base = gdb.snapshot_get(user["user_id"], "misalloc_baseline")
    if base is not None:
        lines.append(f"Baseline at C2: {round(base)} min/day.")
    _say(user, esc("\n".join(lines)))


def cmd_mydata(user, args, msg) -> None:
    if args and args[0] == "delete":
        if len(args) > 1 and args[1] == "confirm":
            gdb.delete_user(user["user_id"])
            _say(user, "Deleted. /start sets up again.")
            return
        _say(user, "This deletes every growth row for your account. Send: /mydata delete confirm")
        return
    data = gdb.export_user(user["user_id"])
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    tg.send_document(payload, f"growth_{user['user_id']}.json", caption="Your growth data.",
                     thread_id=user["thread_id"], chat_id=user["chat_id"])


def cmd_skip(user, args, msg) -> None:
    text = session.skip(user["user_id"])
    if text:
        _say(user, text)


def cmd_visualize(user, args, msg) -> None:
    session.start(user, "visualize")


def cmd_stage2(name: str):
    def inner(user, args, msg) -> None:
        _say(user, f"{name} arrives in stage 2.")
    return inner


_HANDLERS = {
    "start": cmd_start, "why": cmd_why, "status": cmd_status, "morning": cmd_morning, "evening": cmd_evening,
    "now": cmd_now, "aim": cmd_aim, "end": cmd_end, "rest": cmd_rest, "avoid": cmd_avoid, "surf": cmd_surf,
    "start5": cmd_start5, "capacity": cmd_capacity, "predict": cmd_predict, "win": cmd_win, "win_old": cmd_win_old,
    "wins": cmd_wins, "remind": cmd_remind, "excuses": cmd_excuses, "misalloc": cmd_misalloc,
    "mydata": cmd_mydata, "skip": cmd_skip, "survival": cmd_survival, "observe": cmd_observe,
    "visualize": cmd_visualize, "tier": cmd_tier,
}
for _k, _v in STAGE2.items():
    _HANDLERS[_k] = cmd_stage2(_v)
