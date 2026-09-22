"""Touch lifecycle: open → fire (expand steps, send card) → answers via
callbacks / text replies / timers → finish. One touch = one DB row = one
Telegram message edited in place."""
from __future__ import annotations

import json

import telegram_api as tg

from . import clock, ctx as ctxmod, effects, electives, gdb, loader, priority, render, settings, steps
from .render import esc

ARC_KINDS = ("morning", "refocus", "evening")


# ── helpers ───────────────────────────────────────────────────────────────

def _tz(user):
    from zoneinfo import ZoneInfo
    try:
        return ZoneInfo(user["tz"] or settings.TZ_NAME)
    except Exception:
        return settings.TZ


def _state(touch) -> dict:
    st = gdb.loads(touch["state"], {}) or {}
    st.setdefault("steps", [])
    st.setdefault("idx", -1)
    st.setdefault("answers", {})
    st.setdefault("meta", {})
    st.setdefault("opts", {})
    return st


def _save(touch_id: int, state: dict, step_key: str | None = None, **extra) -> None:
    gdb.update_touch(touch_id, state=json.dumps(state, ensure_ascii=False), step=step_key, **extra)


def _current(state: dict) -> dict | None:
    i = state.get("idx", -1)
    stp = state.get("steps", [])
    return stp[i] if 0 <= i < len(stp) else None


def _scope(user, touch, state: dict) -> dict:
    return {"user": user, "day": touch["day"], "touch": touch, "state": state, "answers": state["answers"],
            "ctx": ctxmod.build(user, touch["day"], touch, state), "core": gdb.get_core(user["user_id"])}


def _title(touch) -> str:
    tdef = loader.content().touches.get(touch["kind"], {})
    title = tdef.get("title", touch["kind"].title())
    if touch["kind"] == "refocus" and touch["slot"]:
        title += " " + {3: "A", 5: "B", 7: "C"}.get(int(touch["slot"]), "")
    return title.strip()


# ── open / fire ───────────────────────────────────────────────────────────

def open_touch(user, kind: str, *, slot: int | None = None, day: str | None = None, due_at: str | None = None,
               scheduled: bool = False, challenge_id: int | None = None, meta: dict | None = None) -> int:
    day = day or clock.local_today(_tz(user))
    return gdb.create_touch(user_id=user["user_id"], day=day, kind=kind, slot=slot, challenge_id=challenge_id,
                            chat_id=user["chat_id"], thread_id=user["thread_id"], due_at=due_at,
                            status="scheduled" if scheduled else "pending",
                            state=json.dumps({"meta": meta or {}}, ensure_ascii=False))


def start(user, kind: str, **kw) -> int:
    """Open a command-started touch and send its first card."""
    touch_id = open_touch(user, kind, **kw)
    fire(gdb.get_touch(touch_id))
    return touch_id


def fire(touch) -> bool:
    user = gdb.get_user(touch["user_id"])
    if user is None:
        gdb.update_touch(touch["id"], status="expired")
        return False
    kind, day = touch["kind"], touch["day"]
    tdef = loader.content().touch(kind)
    survival = bool(user["survival"]) and kind in ARC_KINDS
    gdb.ensure_day(user["user_id"], day, 1 if user["survival"] else 0)
    if kind == "morning":
        try:
            priority.refresh_and_snapshot(user["user_id"], day)
        except Exception as e:
            print(f"  growth priority refresh error: {e}")
    prev = _state(touch)
    ctx_d = ctxmod.build(user, day, touch, prev)
    slot_mods = electives.choose(user, day, tdef, ctx_d, survival)
    step_list = steps.expand(tdef, slot_mods, survival, ctxmod.repeat_sources(user, day), gdb.levels(user["user_id"]))
    state = {"steps": step_list, "idx": -1, "answers": {}, "meta": prev.get("meta", {}), "opts": {}}
    window = settings.ANSWER_WINDOW.get(kind, settings.ANSWER_WINDOW["default"])
    now = clock.now()
    gdb.update_touch(touch["id"], state=json.dumps(state, ensure_ascii=False),
                     elective_ids=json.dumps([m["id"] for ms in slot_mods.values() for m in ms]),
                     fired_at=clock.iso(now), expires_at=clock.iso(now + window), status="active")
    gdb.touch_activity(user["user_id"], day)
    return _advance(touch["id"])


# ── stepping ──────────────────────────────────────────────────────────────

def _advance(touch_id: int) -> bool:
    touch = gdb.get_touch(touch_id)
    if touch is None or touch["status"] != "active":
        return False
    state = _state(touch)
    user = gdb.get_user(touch["user_id"])
    skip_keys = set(state["meta"].get("skip_steps", []))
    i = state["idx"]
    while True:
        i += 1
        if i >= len(state["steps"]):
            return _finish(touch, state, None)
        step = state["steps"][i]
        if step["key"] in skip_keys:
            continue
        sc = _scope(user, touch, state)
        if not steps.eligible(step, sc):
            continue
        state["idx"] = i
        _save(touch_id, state, step["key"])
        return _render_current(gdb.get_touch(touch_id), state, sc)


def _jump(touch, state: dict, key: str) -> bool:
    for i, s in enumerate(state["steps"]):
        if s["key"] == key:
            state["idx"] = i - 1
            _save(touch["id"], state, None)
            return _advance(touch["id"])
    return _advance(touch["id"])


def _render_current(touch, state: dict, sc: dict | None = None) -> bool:
    step = _current(state)
    if step is None:
        return _finish(touch, state, None)
    user = gdb.get_user(touch["user_id"])
    sc = sc or _scope(user, touch, state)
    t = step.get("type", "note")
    if step.get("prompt"):
        prompt = steps.fill(step["prompt"], sc, escape=False)
    else:
        prompt = ctxmod.text_from(step.get("text_from", ""), sc)
    if t == "note" and step.get("end"):
        return _finish(touch, state, None, extra=prompt)
    options = None
    if t in ("pick", "multi", "taskpick"):
        options = ctxmod.options_for(step, sc)
        state["opts"][step["key"]] = options
    timer_line = None
    if t == "timer":
        timer = gdb.running_timer_for_touch(touch["id"])
        if timer is None or timer["step"] != step["key"]:
            timer = _start_timer(user, touch, state, step, sc)
        if timer is not None:
            ends = clock.parse(timer["due_at"]).astimezone(_tz(user)).strftime("%H:%M")
            timer_line = f"Ends {ends}."
    selected = state["answers"].get(step["field"]) if t == "multi" else None
    if state.get("shown_idx") != state["idx"]:
        state["shown"] = int(state.get("shown", 0)) + 1
        state["shown_idx"] = state["idx"]
    skip_keys = set(state["meta"].get("skip_steps", []))
    remaining = sum(1 for s in state["steps"][state["idx"] + 1:]
                    if s["key"] not in skip_keys and steps.eligible(s, sc))
    k, n = int(state["shown"]), int(state["shown"]) + remaining
    text, kb = steps.render_step(touch["id"], step, sc, options, prompt, k, n, _title(touch), timer_line, selected)
    msg_id = render.show(touch, text, kb)
    touch = gdb.get_touch(touch["id"])
    if msg_id is None:
        gdb.update_touch(touch["id"], status="send_failed", send_attempts=int(touch["send_attempts"]) + 1)
        _save(touch["id"], state, step["key"])
        return False
    if t == "text":
        gdb.add_prompt(user["user_id"], touch["chat_id"], touch["thread_id"], msg_id, touch["id"], step["key"],
                       clock.iso(clock.now() + settings.PROMPT_EXPIRY))
    else:
        gdb.delete_prompts_for_touch(touch["id"])
    _save(touch["id"], state, step["key"])
    return True


def retry_send(touch) -> None:
    state = _state(touch)
    gdb.update_touch(touch["id"], status="active")
    _render_current(gdb.get_touch(touch["id"]), state)


def _record(touch, state: dict, step: dict, value, sc: dict, skipped: bool = False) -> None:
    field = step.get("field") or step["key"]
    state["answers"][field] = value
    uid, day = touch["user_id"], touch["day"]
    if not skipped and step.get("type", "note") != "note":
        num = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            num = float(value)
        gdb.add_entry(uid, day, touch["id"], step.get("module_id"), field, value, num)
        state["meta"]["last_entry_id"] = gdb._one("SELECT MAX(id) AS m FROM gb_entries")["m"]
    if step.get("effect"):
        effects.run(step["effect"], sc, step, value)
    if step.get("emit") and not skipped and value is not None:
        base = step.get("base_field") or field
        meta = {"field": base, "value": value, base: value, "touch": touch["kind"]}
        if step.get("module_id"):
            meta["module"] = step["module_id"]
        gdb.emit(uid, day, step["emit"], meta)
    if step.get("grade") and isinstance(value, str) and value.strip():
        try:
            from . import gai
            rubric = gai.grade_entry(step.get("module_id") or step.get("base_field") or field, value)
        except Exception as e:
            print(f"  growth grade error: {e}")
            rubric = None
        if rubric:
            entry_id = state["meta"].get("last_entry_id")
            if entry_id:
                gdb.set_entry_rubric(int(entry_id), rubric)
            fu = (rubric.get("follow_up") or "").strip()
            if fu:
                state["meta"].setdefault("follow_ups", []).append(fu)
    gdb.touch_activity(uid, day)


def _after_answer(touch, state: dict, step: dict, value, sc: dict) -> bool:
    end_if = step.get("end_if") or []
    if value is not None and any(str(value) == str(v) for v in end_if):
        ft = step.get("final_text")
        text = ft.get(str(value)) if isinstance(ft, dict) else ft
        return _finish(touch, state, steps.fill(text, sc, escape=False) if text else None)
    if step.get("type", "note") == "note":
        for b in step.get("buttons", []) or []:
            if str(b["value"]) == str(value):
                if b.get("end"):
                    return _finish(touch, state, None)
                if b.get("next"):
                    return _jump(touch, state, b["next"])
                break
    _save(touch["id"], state, step["key"])
    return _advance(touch["id"])


def _finish(touch, state: dict, text: str | None, status: str = "done", extra: str | None = None) -> bool:
    tdef = loader.content().touches.get(touch["kind"], {})
    user = gdb.get_user(touch["user_id"])
    sc = _scope(user, touch, state)
    if not text:
        if tdef.get("done_text_from"):
            text = ctxmod.text_from(tdef["done_text_from"], sc)
        text = text or tdef.get("done_text") or "Done."
    body = esc(text)
    if extra:
        body += f"\n{esc(extra)}"
    for fu in state["meta"].get("follow_ups", []):
        body += f"\n<i>{esc(fu)}</i>"
    render.show(touch, render.card(_title(touch), None, None, body), None)
    now = clock.iso(clock.now())
    gdb.update_touch(touch["id"], status=status, completed_at=now, step=None,
                     state=json.dumps(state, ensure_ascii=False))
    gdb.delete_prompts_for_touch(touch["id"])
    for timer in gdb.running_timers(touch["user_id"]):
        if timer["touch_id"] == touch["id"]:
            gdb.update_timer(timer["id"], status="cancelled")
    if status == "done":
        if tdef.get("on_complete_emit"):
            gdb.emit(touch["user_id"], touch["day"], tdef["on_complete_emit"],
                     {"touch": touch["kind"], "slot": touch["slot"], "electives": state["meta"].get("electives", [])})
        gdb.touch_activity(touch["user_id"], touch["day"], done=True)
    return True


def close(touch, line: str, status: str = "skipped") -> None:
    state = _state(touch)
    render.show(touch, render.card(_title(touch), None, None, esc(line)), None)
    gdb.update_touch(touch["id"], status=status, completed_at=clock.iso(clock.now()), step=None)
    gdb.delete_prompts_for_touch(touch["id"])
    for timer in gdb.running_timers(touch["user_id"]):
        if timer["touch_id"] == touch["id"]:
            gdb.update_timer(timer["id"], status="cancelled")
    _ = state


def expire(touch) -> None:
    if touch["message_id"]:
        render.show(touch, render.card(_title(touch), None, None, "<i>(expired)</i>"), None)
    gdb.update_touch(touch["id"], status="expired", step=None)
    gdb.delete_prompts_for_touch(touch["id"])


# ── timers ────────────────────────────────────────────────────────────────

def _start_timer(user, touch, state: dict, step: dict, sc: dict):
    kind = step.get("kind") or "timer"
    seconds = settings.TIMER_SECONDS.get(kind)
    if step.get("seconds_from"):
        v = steps.lookup(sc, step["seconds_from"])
        try:
            seconds = int(v) if v else seconds
        except (TypeError, ValueError):
            pass
    seconds = int(seconds or 300)
    now = clock.now()
    tid = gdb.create_timer(user_id=user["user_id"], chat_id=touch["chat_id"], thread_id=touch["thread_id"],
                           kind=kind, seconds=seconds, touch_id=touch["id"], step=step["key"],
                           challenge_id=touch["challenge_id"], rest_log_id=state["meta"].get("rest_log_id"),
                           started_at=clock.iso(now), due_at=clock.iso(now + clock.timedelta(seconds=seconds)))
    gdb.emit(user["user_id"], touch["day"], f"{kind}_started", {"seconds": seconds})
    return gdb.get_timer(tid)


def on_timer_fired(timer) -> None:
    gdb.update_timer(timer["id"], status="fired", fired_at=clock.iso(clock.now()))
    touch = gdb.get_touch(timer["touch_id"]) if timer["touch_id"] else None
    if touch is None or touch["status"] != "active":
        return
    state = _state(touch)
    step = _current(state)
    if step is None or step["key"] != timer["step"]:
        return
    user = gdb.get_user(touch["user_id"])
    sc = _scope(user, touch, state)
    _record(touch, state, step, "done", sc)
    ft = step.get("final_text")
    if isinstance(ft, dict) and ft.get("done"):
        _finish(touch, state, steps.fill(ft["done"], sc, escape=False))
        return
    if step.get("after"):
        _jump(touch, state, step["after"])
        return
    _save(touch["id"], state, step["key"])
    _advance(touch["id"])


# ── inbound: callbacks ────────────────────────────────────────────────────

def handle_callback(cq: dict, parts: list[str]) -> None:
    cq_id = cq["id"]
    try:
        touch_id = int(parts[1])
    except (IndexError, ValueError):
        tg.answer_callback_query(cq_id)
        return
    key = parts[2] if len(parts) > 2 else ""
    raw = ":".join(parts[3:])
    touch = gdb.get_touch(touch_id)
    msg = cq.get("message") or {}
    if touch is None or (msg.get("chat") or {}).get("id") != touch["chat_id"]:
        tg.answer_callback_query(cq_id, "Expired.")
        return
    if touch["status"] != "active":
        tg.answer_callback_query(cq_id, "That card is closed.")
        return
    if key == "x":
        close(touch, "Closed.")
        tg.answer_callback_query(cq_id)
        return
    state = _state(touch)
    step = _current(state)
    if step is None or step["key"] != key:
        tg.answer_callback_query(cq_id, "That step's done.")
        return
    user = gdb.get_user(touch["user_id"])
    sc = _scope(user, touch, state)
    t = step.get("type", "note")
    if t == "multi":
        options = state["opts"].get(key) or ctxmod.options_for(step, sc)
        cur = list(state["answers"].get(step["field"]) or [])
        if raw != "ok":
            val = steps.parse_value(step, raw, options)
            if val is None:
                tg.answer_callback_query(cq_id, "Expired option.")
                return
            if val in cur:
                cur.remove(val)
            elif len(cur) < int(step.get("max", 99)):
                cur.append(val)
            state["answers"][step["field"]] = cur
            _save(touch_id, state, key)
            _render_current(gdb.get_touch(touch_id), state, sc)
            tg.answer_callback_query(cq_id)
            return
        if len(cur) < int(step.get("min", 0)):
            tg.answer_callback_query(cq_id, f"Pick at least {step['min']}.")
            return
        value = cur
    elif t == "timer":
        if raw != "stop":
            tg.answer_callback_query(cq_id)
            return
        for timer in gdb.running_timers(user["user_id"]):
            if timer["touch_id"] == touch_id:
                gdb.update_timer(timer["id"], status="cancelled")
        value = "stopped"
    elif t == "text":
        if raw != "skip":
            tg.answer_callback_query(cq_id)
            return
        _record(touch, state, step, None, sc, skipped=True)
        tg.answer_callback_query(cq_id)
        _save(touch_id, state, key)
        _advance(touch_id)
        return
    else:
        options = state["opts"].get(key) or (ctxmod.options_for(step, sc) if t in ("pick", "taskpick") else None)
        value = steps.parse_value(step, raw, options)
        if value is None:
            tg.answer_callback_query(cq_id, "Expired option.")
            return
    _record(touch, state, step, value, sc)
    tg.answer_callback_query(cq_id)
    _after_answer(gdb.get_touch(touch_id), state, step, value, sc)


# ── inbound: text ─────────────────────────────────────────────────────────

def _prompt_is_live(prompt) -> bool:
    return prompt is not None and prompt["expires_at"] > clock.iso(clock.now())


def wants_reply(user_id: int, reply_id: int) -> bool:
    if gdb.prompt_by_message(user_id, reply_id) is not None:
        return True
    touch = gdb.touch_by_message(user_id, reply_id)
    return touch is not None and touch["status"] == "active"


def handle_text(user_id: int, reply_id: int, text: str, msg: dict) -> None:
    prompt = gdb.prompt_by_message(user_id, reply_id)
    if prompt is not None and _prompt_is_live(prompt) and _answer_prompt(prompt, text):
        return
    touch = gdb.touch_by_message(user_id, reply_id)
    if touch is not None:
        render.plain(touch["chat_id"], touch["thread_id"], "That step's done — use the buttons, or /skip.")


def claim_text(user_id: int, chat_id: int, thread_id: int | None, text: str, msg: dict) -> bool:
    prompt = gdb.latest_prompt(user_id)
    if prompt is None:
        return False
    if not _prompt_is_live(prompt):
        gdb.delete_prompts_for_touch(prompt["touch_id"])
        return False
    last = tg.last_sent_message_id(chat_id, thread_id)
    if last is None or last != prompt["message_id"]:
        return False
    return _answer_prompt(prompt, text)


def _answer_prompt(prompt, text: str) -> bool:
    touch = gdb.get_touch(prompt["touch_id"])
    if touch is None or touch["status"] != "active":
        gdb.delete_prompts_for_touch(prompt["touch_id"])
        return False
    state = _state(touch)
    step = _current(state)
    if step is None or step["key"] != prompt["step"] or step.get("type") != "text":
        gdb.delete_prompts_for_touch(prompt["touch_id"])
        return False
    user = gdb.get_user(touch["user_id"])
    sc = _scope(user, touch, state)
    value = text.strip()[:2000]
    _record(touch, state, step, value, sc)
    gdb.delete_prompts_for_touch(touch["id"])
    _after_answer(gdb.get_touch(touch["id"]), state, step, value, sc)
    return True


def skip(user_id: int) -> str:
    prompt = gdb.latest_prompt(user_id)
    if prompt is None or not _prompt_is_live(prompt):
        return "Nothing to skip."
    touch = gdb.get_touch(prompt["touch_id"])
    if touch is None or touch["status"] != "active":
        gdb.delete_prompts_for_touch(prompt["touch_id"])
        return "Nothing to skip."
    state = _state(touch)
    step = _current(state)
    if step is None or step["key"] != prompt["step"]:
        return "Nothing to skip."
    user = gdb.get_user(touch["user_id"])
    sc = _scope(user, touch, state)
    _record(touch, state, step, None, sc, skipped=True)
    _save(touch["id"], state, step["key"])
    _advance(touch["id"])
    return ""


def cancel(user_id: int) -> str | None:
    for timer in gdb.running_timers(user_id):
        gdb.update_timer(timer["id"], status="cancelled")
        touch = gdb.get_touch(timer["touch_id"]) if timer["touch_id"] else None
        if touch is not None and touch["status"] == "active":
            close(touch, "Timer cancelled.", "cancelled")
        return f"Growth: {timer['kind']} timer cancelled."
    active = gdb.active_touches(user_id)
    if not active:
        return None
    touch = active[0]
    status = "skipped" if touch["kind"] in ARC_KINDS else "cancelled"
    close(touch, "Cancelled.", status)
    return f"Growth: {touch['kind']} cancelled."


def fire_scheduled_now(user, kind: str) -> int:
    """/morning, /evening: fire today's scheduled row early, else an ad-hoc one."""
    day = clock.local_today(_tz(user))
    for t in gdb.touches_for_day(user["user_id"], day):
        if t["kind"] == kind and t["status"] == "scheduled":
            gdb.update_touch(t["id"], due_at=clock.iso(clock.now()))
            fire(gdb.get_touch(t["id"]))
            return t["id"]
        if t["kind"] == kind and t["status"] == "active":
            retry_send(t)
            return t["id"]
    return start(user, kind)
