"""Per-render context: the `ctx.*` values that YAML conditions and templates
read, plus the text_from / options_from registries."""
from __future__ import annotations

from typing import Any

from . import bank, clock, excuses, gdb, levels, loader, priority, settings
from .render import esc

CORE_FIELDS = ["why", "eulogy", "fixed_areas", "top_interest", "signature_strengths", "avoided_tasks",
               "rest_restores", "rest_feels_like", "franklin_virtues", "roles"]
# rest_restores / rest_feels_like are stored together as core field `rest_menu`
CORE_STORED = {"rest_restores": "rest_menu", "rest_feels_like": "rest_menu"}


def core_missing(core: dict) -> list[str]:
    out = []
    for f in CORE_FIELDS:
        stored = CORE_STORED.get(f, f)
        v = core.get(stored)
        if f == "rest_restores" and isinstance(v, dict) and v.get("restores"):
            continue
        if f == "rest_feels_like" and isinstance(v, dict) and "feels_like" in v:
            continue
        if f in CORE_STORED:
            out.append(f)
            continue
        if v in (None, "", [], {}):
            out.append(f)
    return out


def rest_menu(core: dict) -> dict:
    rm = core.get("rest_menu") or {}
    if not isinstance(rm, dict):
        rm = {}
    return {"restores": rm.get("restores") or [], "feels_like": rm.get("feels_like") or []}


def observation_week(user, day: str) -> tuple[int, int]:
    """(week 1..N, weekday 0..6) for the observation cycle."""
    c = loader.content()
    n_weeks = len(c.observation["weeks"])
    started = user["obs_started_on"] or user["created_at"][:10]
    days = max(0, (clock.parse(day + "T00:00:00+00:00") - clock.parse(started + "T00:00:00+00:00")).days)
    return days // 7 % n_weeks + 1, clock.weekday(day)


def franklin_virtue(user, core: dict, day: str) -> dict | None:
    chosen = core.get("franklin_virtues") or []
    if not chosen:
        return None
    c = loader.content()
    by_id = {v["id"]: v for v in c.copy["virtues"]}
    by_name = {v["name"].lower(): v for v in c.copy["virtues"]}
    vs = [by_id.get(str(x)) or by_name.get(str(x).lower()) or {"id": str(x), "name": str(x), "precept": ""}
          for x in chosen]
    started = user["obs_started_on"] or user["created_at"][:10]
    weeks = max(0, (clock.parse(day + "T00:00:00+00:00") - clock.parse(started + "T00:00:00+00:00")).days) // 7
    return vs[weeks % len(vs)]


def _rest_budget_items(day_row) -> list[dict]:
    items = gdb.loads(day_row["rest_budget"], []) if day_row else []
    return items if isinstance(items, list) else []


def rest_left_minutes(user_id: int, day: str) -> int | None:
    day_row = gdb.get_day(user_id, day)
    items = _rest_budget_items(day_row)
    if not items:
        return None
    budget = sum(int(i.get("minutes") or 0) for i in items)
    used = sum(int(r["actual_min"] or 0) for r in gdb.rest_logs_for_day(user_id, day))
    return max(0, budget - used)


def build(user, day: str, touch=None, state: dict | None = None) -> dict[str, Any]:
    uid = user["user_id"]
    core = gdb.get_core(uid)
    day_row = gdb.get_day(uid, day)
    answers = (state or {}).get("answers", {})
    lv = gdb.levels(uid)
    c = loader.content()
    ctx: dict[str, Any] = {}

    plist = priority.day_list(uid, day)
    ctx["has_plist"] = bool(plist)
    ctx["p_source"] = priority.source_label(day_row["p_source"] if day_row else None)
    ctx["p_lines"] = "\n".join(f"P{p['p']} · {p['title']}" for p in plist[:3]) if plist else "(no ranked tasks)"
    for p in plist:
        ctx[f"p{p['p']}_title"] = p["title"]
    ctx["p1_title"] = ctx.get("p1_title") or "P1"

    rm = rest_menu(core)
    ctx["has_restores"] = bool(rm["restores"])
    ctx["has_feels_like"] = bool(rm["feels_like"])
    left = rest_left_minutes(uid, day)
    ctx["rest_left_min"] = left
    ctx["rest_left_line"] = f"{left} min left in today's budget." if left is not None else ""

    tier_id = (day_row["tier"] if day_row and day_row["tier"] else user["tier"]) or "t30"
    tier_def = c.tier(tier_id)
    ctx["tier"] = tier_id
    ctx["tiers_unlocked"] = lv.get("tier", 1)
    ctx["challenges_per_day"] = 1 if user["survival"] else int(tier_def.get("challenges_per_day", 2))
    ctx["survival"] = bool(user["survival"])
    ctx["capacity"] = day_row["capacity"] if day_row else None
    ctx["wont_today"] = (day_row["wont"] if day_row else None) or None

    logs = gdb.refocus_logs(uid, [day])
    ctx["misalloc_today"] = any(r["on_kind"] == "other" or (r["on_p"] or 0) >= 3 or r["rest_mode"] == "drifted"
                                for r in logs)
    ctx["excuse_today"] = bool(gdb.excuse_logs(uid, day) and any(l["day"] == day for l in gdb.excuse_logs(uid, day)))
    ex = answers.get("excuse_id")
    ctx["excuse_open"] = ex not in (None, "note_only", "")

    week, wd = observation_week(user, day)
    w = c.obs_week(week)
    ctx["obs_week"] = week
    ctx["obs_title"] = w["title"]
    prompts = w.get("daily_prompts") or []
    if wd == 6 and w.get("experiment", {}).get("daily_prompt"):
        ctx["observation_prompt"] = f"Week {week}, {w['title']}. " + w["experiment"]["daily_prompt"]
    elif prompts:
        ctx["observation_prompt"] = f"Week {week}, {w['title']}. " + prompts[wd % len(prompts)]
    else:
        ctx["observation_prompt"] = w.get("microscope", {}).get("daily_prompt", "What did you notice today?")

    fv = franklin_virtue(user, core, day)
    ctx["franklin_virtue_line"] = f"{fv['name']}: {fv['precept']} " if fv else ""

    missing = core_missing(core)
    ctx["next_core_field"] = missing[0] if missing else None
    ctx["next_core_prompt"] = c.copy["core_prompts"].get(missing[0], "") if missing else ""
    ctx["core_why"] = core.get("why")
    ctx["core_complete"] = not missing
    ctx["has_wins"] = gdb.win_count(uid) > 0
    ctx["has_roles"] = bool(core.get("roles"))
    ctx["top_interest"] = core.get("top_interest") or "that"

    cur, best = levels.streak(uid, day)
    ctx["streak_current"] = cur
    ctx["streak_best"] = best
    ctx["levels"] = {k: v for k, v in lv.items() if k in ("A", "B", "C")}
    ctx["weekday"] = wd

    intro = levels.intro_lines(user, day)
    ctx["has_intro"] = bool(intro)
    ctx["intro_text"] = "\n".join(intro)

    if touch is not None and touch["kind"] == "end" and touch["challenge_id"]:
        ch = gdb.get_challenge(touch["challenge_id"])
        est = ch["est_min"] if ch else None
        act = answers.get("actual_min")
        if est and act:
            ctx["est_vs_actual"] = f"Estimated {est}, actual {act}. Next?"
        else:
            ctx["est_vs_actual"] = "Next?"
    if touch is not None and touch["kind"] == "rest":
        ctx["rest_seconds"] = int(((state or {}).get("meta", {}) or {}).get("rest_seconds") or 0) or 1200
    return ctx


def repeat_sources(user, day: str) -> dict[str, list[dict]]:
    uid = user["user_id"]
    chs = [{"id": ch["n"], "n": ch["n"], "title": ch["task_title"] or "Other"}
           for ch in gdb.challenges_for_day(uid, day) if ch["status"] in ("planned", "started", "ended")]
    day_row = gdb.get_day(uid, day)
    rest = [{"id": i, "title": f"{it.get('activity', 'rest')}, {it.get('minutes', '?')} min"}
            for i, it in enumerate(_rest_budget_items(day_row))]
    return {"challenges_today": chs, "rest_items_today": rest}


# ── text_from registry ────────────────────────────────────────────────────

def _t_morning_intro(sc: dict) -> str:
    return sc["ctx"].get("intro_text") or ""


def _t_morning_summary(sc: dict) -> str:
    a = sc["answers"]
    d = gdb.get_day(sc["user"]["user_id"], sc["day"])
    parts = []
    if d and d["capacity"] is not None:
        parts.append(f"Capacity {d['capacity']} h, sleep {d['sleep'] or '?'}.")
    plist = priority.day_list(sc["user"]["user_id"], sc["day"])
    if plist:
        parts.append(" · ".join(f"P{p['p']} {p['title']}" for p in plist[:3]))
    chs = gdb.challenges_for_day(sc["user"]["user_id"], sc["day"])
    if chs:
        parts.append("Challenges: " + "; ".join(f"{ch['n']} · {ch['task_title']}" for ch in chs))
    if d and d["wont"]:
        parts.append(f"I won't: {d['wont']}.")
    left = rest_left_minutes(sc["user"]["user_id"], sc["day"])
    if left is not None:
        parts.append(f"Rest budget {left} min.")
    if a.get("failure_point"):
        parts.append(f"Failure point: {a['failure_point']}")
    return "\n".join(parts) or "Morning logged."


def _t_excuse_counter(sc: dict) -> str:
    ex_id = sc["answers"].get("excuse_id")
    row = gdb.get_excuse(int(ex_id)) if ex_id not in (None, "new", "note_only", "") else None
    if row is None and sc["answers"].get("excuse_new"):
        row = gdb.get_excuse(int(sc["state"].get("meta", {}).get("new_excuse_id") or 0))
    return excuses.counter_text(sc["user"], row, sc)


def _t_remind_line(sc: dict) -> str:
    row = bank.remind(sc["user"]["user_id"])
    return bank.format_line(row) if row else "No wins stored yet. /win_old adds one."


def _t_spark_line(sc: dict) -> str:
    row = bank.remind(sc["user"]["user_id"])
    return bank.format_line(row) if row else ""


def _t_urge_offer(sc: dict) -> str:
    return "An excuse was logged today. Surf the urge for 90 s first, or start now."


def _t_self_forgiveness(sc: dict) -> str:
    return excuses.forgiveness_line(sc["user"]["user_id"])


def _t_observation_intro(sc: dict) -> str:
    c = loader.content()
    week, _ = observation_week(sc["user"], sc["day"])
    w = c.obs_week(week)
    return (f"Week {week}: {w['title']}.\n{w['theme'].strip()}\n\nUnder the microscope: {w['microscope']['text'].strip()}"
            f"\n\nExperiment: {w['experiment']['text'].strip()}")


def _t_why_text(sc: dict) -> str:
    return f"Your why: {sc['core'].get('why', '')}"


def _t_visualization_prompt(sc: dict) -> str:
    from . import visualization
    return visualization.prompt_for(sc["user"], sc["day"], sc["state"])


def _t_rest_back_line(sc: dict) -> str:
    meta = sc["state"].get("meta", {})
    item = meta.get("rest_item") or "Rest"
    mins = meta.get("rest_minutes") or "?"
    return f"{item}, {mins} min, done. Back to P1 · {sc['ctx'].get('p1_title', 'P1')}?"


def _t_surf_done_line(sc: dict) -> str:
    return f"90 s. 5 min on P1 · {sc['ctx'].get('p1_title', 'P1')}?"


def _t_aim_summary(sc: dict) -> str:
    a = sc["answers"]
    ch = gdb.get_challenge(sc["touch"]["challenge_id"]) if sc["touch"]["challenge_id"] else None
    title = ch["task_title"] if ch else "task"
    parts = [f"Started: {title}."]
    if a.get("done_looks_like"):
        parts.append(f"Done looks like: {a['done_looks_like']}")
    if a.get("est_min"):
        parts.append(f"{a['est_min']} min. /end when you stop.")
    return "\n".join(parts)


def _t_end_summary(sc: dict) -> str:
    a = sc["answers"]
    ch = gdb.get_challenge(sc["touch"]["challenge_id"]) if sc["touch"]["challenge_id"] else None
    parts = []
    if ch:
        parts.append(f"{ch['task_title']}: moved {a.get('moved', '?')}/5.")
        if ch["est_min"] and a.get("actual_min"):
            parts.append(f"Estimated {ch['est_min']}, actual {a['actual_min']}.")
    if a.get("next_step"):
        parts.append(f"Next: {a['next_step']}")
    elif a.get("next_choice") == "rest":
        parts.append(sc["ctx"].get("rest_left_line") or "Rest budget: /rest.")
    return "\n".join(parts) or "Ended."


def _t_evening_summary(sc: dict) -> str:
    cur = sc["ctx"].get("streak_current", 0)
    best = sc["ctx"].get("streak_best", 0)
    line = f"{cur} days with at least one touch. Best: {best}."
    return "Evening logged. " + line


def _t_onboard_intro(sc: dict) -> str:
    return loader.content().copy["onboard_intro"].strip()


def _t_onboard_summary(sc: dict) -> str:
    u = sc["user"]
    text = loader.content().copy["onboard_outro"].strip()
    windows = ", ".join(gdb.loads(u["refocus_windows"], settings.DEFAULT_REFOCUS_WINDOWS))
    return text.format(morning_time=u["morning_time"], windows=windows, evening_time=u["evening_time"])


def _t_onboard_outro(sc: dict) -> str:
    return _t_onboard_summary(sc)


TEXTS = {
    "morning_intro": _t_morning_intro, "morning_summary": _t_morning_summary,
    "excuse_counter": _t_excuse_counter, "remind_line": _t_remind_line, "spark_line": _t_spark_line,
    "urge_offer": _t_urge_offer, "self_forgiveness": _t_self_forgiveness,
    "observation_intro": _t_observation_intro, "why_text": _t_why_text,
    "visualization_prompt": _t_visualization_prompt, "rest_back_line": _t_rest_back_line,
    "surf_done_line": _t_surf_done_line, "aim_summary": _t_aim_summary, "end_summary": _t_end_summary,
    "evening_summary": _t_evening_summary, "onboard_intro": _t_onboard_intro,
    "onboard_summary": _t_onboard_summary, "onboard_outro": _t_onboard_outro,
}


def text_from(name: str, sc: dict) -> str:
    fn = TEXTS.get(name)
    if fn is None:
        return ""
    try:
        return fn(sc) or ""
    except Exception as e:  # never let a text source break a card
        print(f"  growth text_from {name} error: {e}")
        return ""


# ── options_from registry ─────────────────────────────────────────────────

def _o_p_buttons(sc: dict, step: dict) -> list[dict]:
    plist = priority.day_list(sc["user"]["user_id"], sc["day"])
    opts = [{"value": f"p{p['p']}", "label": f"P{p['p']} · {p['title'][:24]}"} for p in plist[:3]]
    if not opts:
        opts = [{"value": "p1", "label": "P1"}, {"value": "p2", "label": "P2"}, {"value": "p3", "label": "P3"}]
    opts += [{"value": "rest", "label": "Rest"}, {"value": "other", "label": "Other"}]
    return opts


def _o_taskpick(sc: dict, step: dict) -> list[dict]:
    plist = priority.day_list(sc["user"]["user_id"], sc["day"])
    allowed = step.get("from") or ["p1", "p2", "p3", "p4", "p5"]
    opts = [{"value": f"p{p['p']}", "label": f"P{p['p']} · {p['title'][:24]}"}
            for p in plist if f"p{p['p']}" in allowed]
    opts.append({"value": "other", "label": "Other"})
    opts.append({"value": "none", "label": "None"})
    return opts


def _o_excuses(family: int):
    def inner(sc: dict, step: dict) -> list[dict]:
        rows = gdb.excuses_for_family(sc["user"]["user_id"], family, limit=6)
        opts = [{"value": r["id"], "label": r["text"][:40]} for r in rows]
        opts.append({"value": "new", "label": "New…"})
        opts.append({"value": "note_only", "label": "Just noting it"})
        return opts
    return inner


def _o_restores(sc: dict, step: dict) -> list[dict]:
    rm = rest_menu(sc["core"])
    return [{"value": i, "label": f"{it.get('activity', '?')} {it.get('minutes', '')} min".strip()}
            for i, it in enumerate(rm["restores"])]


def _o_feels_like(sc: dict, step: dict) -> list[dict]:
    rm = rest_menu(sc["core"])
    opts = [{"value": i, "label": str(it)[:30]} for i, it in enumerate(rm["feels_like"])]
    opts.append({"value": "none", "label": "None today"})
    return opts


def _o_budget_today(sc: dict, step: dict) -> list[dict]:
    d = gdb.get_day(sc["user"]["user_id"], sc["day"])
    items = _rest_budget_items(d)
    opts = [{"value": i, "label": f"{it.get('activity', '?')} {it.get('minutes', '')} min".strip()}
            for i, it in enumerate(items)]
    opts.append({"value": "other15", "label": "Other, 15 min"})
    return opts


def _o_tiers(sc: dict, step: dict) -> list[dict]:
    return [{"value": t["id"], "label": t["label"]} for t in levels.unlocked_tiers(sc["user"]["user_id"])]


def _o_block_minutes(sc: dict, step: dict) -> list[dict]:
    return [{"value": m, "label": str(m)} for m in loader.content().copy["block_minutes"]]


def _o_strengths(sc: dict, step: dict) -> list[dict]:
    return [{"value": i, "label": s} for i, s in enumerate(loader.content().copy["strengths"])]


def _o_virtues(sc: dict, step: dict) -> list[dict]:
    return [{"value": v["id"], "label": v["name"]} for v in loader.content().copy["virtues"]]


OPTIONS = {
    "p_buttons": _o_p_buttons, "taskpick": _o_taskpick,
    "excuses.family1": _o_excuses(1), "excuses.family2": _o_excuses(2),
    "rest_menu.restores": _o_restores, "rest_menu.feels_like": _o_feels_like,
    "rest_menu.budget_today": _o_budget_today, "tiers.unlocked": _o_tiers,
    "block_minutes": _o_block_minutes, "strengths": _o_strengths, "virtues": _o_virtues,
}


def options_for(step: dict, sc: dict) -> list[dict]:
    if step.get("options"):
        return [{"value": o["value"], "label": str(o["label"])} for o in step["options"]]
    name = step.get("options_from")
    if step.get("type") == "taskpick":
        name = "taskpick"
    fn = OPTIONS.get(name or "")
    if fn is None:
        return []
    try:
        return fn(sc, step)
    except Exception as e:
        print(f"  growth options_from {name} error: {e}")
        return []
