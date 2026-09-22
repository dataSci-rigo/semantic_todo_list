"""Elective modules: which ones fill a touch's slots today."""
from __future__ import annotations

from . import clock, gdb, loader, steps


def _frequency_ok(module: dict, exp, day: str) -> bool:
    freq = str(module.get("frequency", "daily"))
    last = exp["last_day"] if exp else None
    if freq == "daily":
        return last != day
    if freq == "once":
        return exp is None or int(exp["exposures"]) == 0
    if freq.startswith("weekly:"):
        want = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(freq.split(":", 1)[1][:3].lower())
        return clock.weekday(day) == want and last != day
    if freq.startswith("every_n_days:"):
        n = int(freq.split(":", 1)[1])
        if last is None:
            return True
        gap = (clock.parse(day + "T00:00:00+00:00") - clock.parse(last + "T00:00:00+00:00")).days
        return gap >= n
    return last != day


def choose(user, day: str, touch_def: dict, ctx_d: dict, survival: bool) -> dict[str, list[dict]]:
    """slot id → chosen modules (in order). Bumps exposures for chosen modules."""
    c = loader.content()
    uid = user["user_id"]
    lv = gdb.levels(uid)
    budget = int(touch_def.get("max_electives", 1))
    surv_slots = set((touch_def.get("survival") or {}).get("slots", []))
    scope = {"ctx": ctx_d}
    out: dict[str, list[dict]] = {}
    for raw in touch_def.get("steps", []):
        if "slot" not in raw:
            continue
        slot = raw["slot"]
        if survival and slot not in surv_slots:
            continue
        for m in c.modules_for_slot(touch_def["id"], slot):
            if lv.get(m.get("track", "A"), 1) < int(m.get("min_level", 1)):
                continue
            exp = gdb.exposure(uid, m["id"])
            if exp and exp["dropped"]:
                continue
            if not _frequency_ok(m, exp, day):
                continue
            if not steps.evaluate(m.get("when"), scope):
                continue
            always = bool(m.get("always") or raw.get("always"))
            if not always and budget <= 0:
                continue
            out.setdefault(slot, []).append(m)
            gdb.bump_exposure(uid, m["id"], day)
            if not always:
                budget -= 1
            break  # one module per slot
    return out
