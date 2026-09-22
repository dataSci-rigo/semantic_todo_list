"""Generic metric evaluator (count / days / ratio / measure) over gb_events,
level-ups as system wins, tiers, streaks, morning intro lines."""
from __future__ import annotations

from typing import Any

from . import bank, clock, gdb, loader, measures, settings

TRACK_NAMES = {"A": "Frame", "B": "Approach", "C": "Aim"}


# ── metric evaluation ─────────────────────────────────────────────────────

def _where_ok(meta: dict, where: dict | None) -> bool:
    for k, want in (where or {}).items():
        if k.endswith("_gte"):
            v = meta.get(k[:-4])
            if v is None or float(v) < float(want):
                return False
        elif k.endswith("_lte"):
            v = meta.get(k[:-4])
            if v is None or float(v) > float(want):
                return False
        elif k.endswith("_in"):
            if str(meta.get(k[:-3])) not in [str(x) for x in want]:
                return False
        else:
            if str(meta.get(k)) != str(want):
                return False
    return True


def _window_days(user_id: int, today: str, metric: dict) -> list[str] | None:
    w = metric.get("window_days")
    if not w:
        return None
    days = gdb.active_days(user_id, today, int(w))
    # include days without a gb_days row? Only planned days count as active; fall back to calendar
    if len(days) < int(w):
        cal = [clock.day_offset(today, -i) for i in range(int(w))]
        known = set(days)
        rows = {d["day"]: d for d in gdb.recent_days(user_id, today, int(w) * 2)}
        for d in cal:
            if d in known:
                continue
            r = rows.get(d)
            if r is None or not r["survival"]:
                known.add(d)
        days = sorted(known, reverse=True)[: int(w)]
    return days


def evaluate_metric(user_id: int, today: str, metric: dict) -> dict[str, Any]:
    """{id, current, target, passed, label}"""
    kind = metric["kind"]
    days = _window_days(user_id, today, metric)
    out: dict[str, Any] = {"id": metric.get("id"), "kind": kind}
    if kind in ("count", "days", "ratio"):
        evs = gdb.events(user_id, metric["event"], days)
        evs = [e for e in evs if _where_ok(gdb.loads(e["meta_json"], {}) or {}, metric.get("where"))]
        if kind == "count":
            if metric.get("distinct"):
                key = metric["distinct"]
                cur = len({str((gdb.loads(e["meta_json"], {}) or {}).get(key)) for e in evs})
            else:
                cur = len(evs)
            target = int(metric["min"])
            out.update(current=cur, target=target, passed=cur >= target, label=f"{cur}/{target}")
        elif kind == "days":
            per_day: dict[str, int] = {}
            for e in evs:
                per_day[e["day"]] = per_day.get(e["day"], 0) + 1
            need = int(metric.get("per_day_min", 1))
            cur = sum(1 for n in per_day.values() if n >= need)
            target = int(metric["min"])
            out.update(current=cur, target=target, passed=cur >= target, label=f"{cur}/{target} days")
        else:
            num = [e for e in evs if _where_ok(gdb.loads(e["meta_json"], {}) or {}, metric.get("numerator_where"))]
            n = len(evs)
            ratio = (len(num) / n) if n else 0.0
            target = float(metric["min_ratio"])
            passed = n >= int(metric.get("min_n", 1)) and ratio >= target
            out.update(current=round(ratio, 2), target=target, passed=passed,
                       label=f"{len(num)}/{n} ({int(ratio * 100)}%)")
        return out
    # measure
    fn = measures.MEASURES.get(metric["measure"])
    if fn is None:
        out.update(current=None, target=None, passed=False, label="unknown measure")
        return out
    value, n = fn(user_id, metric, days or [])
    out["current"] = value
    out["n"] = n
    if metric.get("informational"):
        out.update(passed=True, target=None, label=f"{value if value is not None else '—'} over {n}")
        return out
    if "min_days_measured" in metric:
        need = int(metric["min_days_measured"])
        out.update(target=need, passed=n >= need, label=f"{n}/{need} days measured")
        return out
    if "max_mean" in metric:
        target = float(metric["max_mean"])
        need = int(metric.get("last_n", 1))
        passed = value is not None and n >= need and value <= target
        out.update(target=target, passed=passed, label=f"{round(value, 2) if value is not None else '—'} ≤ {target} over {n}")
        return out
    if "max_ratio_to_snapshot" in metric:
        base = gdb.snapshot_get(user_id, metric["max_ratio_to_snapshot"])
        factor = float(metric.get("factor", 0.5))
        if base is None or value is None:
            out.update(target=None, passed=False, label="no baseline yet")
        else:
            target = base * factor
            out.update(target=round(target, 1), passed=value <= target,
                       label=f"{round(value, 1)} vs target {round(target, 1)} (baseline {round(base, 1)})")
        return out
    out.update(target=None, passed=value is not None, label=str(value))
    return out


def _next_level_def(track: dict, current: int) -> dict | None:
    for lvl in track.get("levels", []):
        if int(lvl["level"]) == current + 1:
            return lvl
    return None


def progress(user_id: int, today: str) -> dict[str, dict]:
    """Per track: current level, next level name, metric results."""
    c = loader.content()
    lv = gdb.levels(user_id)
    out = {}
    for tid, track in c.track_defs.items():
        cur = lv.get(tid, 1)
        nxt = _next_level_def(track, cur)
        results = [evaluate_metric(user_id, today, m) for m in (nxt or {}).get("metrics", [])] if nxt else []
        out[tid] = {"level": cur, "name": _level_name(track, cur), "next": nxt, "results": results}
    return out


def _level_name(track: dict, level: int) -> str:
    for lvl in track.get("levels", []):
        if int(lvl["level"]) == level:
            return lvl.get("name", "")
    return ""


# ── nightly ───────────────────────────────────────────────────────────────

def nightly(user, today: str) -> list[str]:
    """Evaluate level-ups and tier unlocks; returns announcement lines."""
    uid = user["user_id"]
    if user["survival"]:
        return []
    c = loader.content()
    lines = []
    lv = gdb.levels(uid)
    for tid, track in c.track_defs.items():
        cur = lv.get(tid, 1)
        nxt = _next_level_def(track, cur)
        if not nxt:
            continue
        results = [evaluate_metric(uid, today, m) for m in nxt.get("metrics", [])]
        if results and all(r["passed"] for r in results):
            evidence = "; ".join(f"{r['id']} {r['label']}" for r in results)
            new_level = int(nxt["level"])
            gdb.set_level(uid, tid, new_level, {"evidence": evidence, "announced": False})
            for name, spec in (nxt.get("on_reach_snapshot") or {}).items():
                fn = measures.MEASURES.get(spec["measure"])
                if fn:
                    days = _window_days(uid, today, spec) or []
                    value, _ = fn(uid, spec, days)
                    gdb.snapshot_set(uid, name, value)
            text = c.copy["levelup"].format(track_name=TRACK_NAMES.get(tid, tid), track=tid, level=new_level,
                                            evidence=evidence)
            bank.add_system(uid, f"{TRACK_NAMES.get(tid, tid)} {tid}{new_level} reached. {evidence}", today)
            gdb.emit(uid, today, "level_up", {"track": tid, "level": new_level})
            lines.append(text)
    # tiers
    cur_tier = lv.get("tier", 1)
    for tier in c.tier_defs:
        if int(tier["level"]) != cur_tier + 1:
            continue
        results = [evaluate_metric(uid, today, m) for m in tier.get("unlock", [])]
        if results and all(r["passed"] for r in results):
            gdb.set_level(uid, "tier", int(tier["level"]), {"announced": False})
            lines.append(c.copy["tier_unlock"].format(label=tier["label"]))
            gdb.emit(uid, today, "tier_unlocked", {"tier": tier["id"]})
    return lines


def unlocked_tiers(user_id: int) -> list[dict]:
    lv = gdb.levels(user_id).get("tier", 1)
    return [t for t in loader.content().tier_defs if int(t["level"]) <= lv]


# ── streaks ───────────────────────────────────────────────────────────────

def streak(user_id: int, today: str) -> tuple[int, int]:
    days = gdb.activity_days(user_id)
    if not days:
        return 0, 0
    dayset = set(days)
    best = 0
    run = 0
    prev = None
    for d in days:
        if prev is not None and clock.day_offset(prev, 1) == d:
            run += 1
        else:
            run = 1
        best = max(best, run)
        prev = d
    cur = 0
    d = today
    if d not in dayset:
        d = clock.day_offset(today, -1)
    while d in dayset:
        cur += 1
        d = clock.day_offset(d, -1)
    return cur, best


def intro_lines(user, today: str) -> list[str]:
    """Unannounced level-ups, streak milestone, survival suggestion."""
    uid = user["user_id"]
    lines = []
    for row in gdb.level_rows(uid):
        ev = gdb.loads(row["evidence"], {}) or {}
        if not ev.get("announced"):
            if row["track"] == "tier":
                tier = next((t for t in loader.content().tier_defs if int(t["level"]) == int(row["level"])), None)
                if tier:
                    lines.append(loader.content().copy["tier_unlock"].format(label=tier["label"]))
            else:
                lines.append(f"{TRACK_NAMES.get(row['track'], row['track'])} {row['track']}{row['level']} reached. "
                             f"{ev.get('evidence', '')}".strip())
            ev["announced"] = True
            gdb._update("gb_levels", "user_id = ? AND track = ? AND level = ?",
                        (uid, row["track"], row["level"]), evidence=gdb._j(ev))
    cur, _ = streak(uid, today)
    if cur in settings.STREAK_MILESTONES:
        lines.append(loader.content().copy["streak_milestone"].format(n=cur))
    if not user["survival"]:
        recent = gdb.recent_days(uid, clock.day_offset(today, -1), 2)
        if len(recent) == 2 and all(r["capacity"] == 0 for r in recent):
            lines.append(loader.content().copy["survival_suggest"])
    return lines


def status_text(user, today: str) -> str:
    uid = user["user_id"]
    c = loader.content()
    prog = progress(uid, today)
    target = c.tracks.get("targets", {}).get("pre_baby", {})
    lines = []
    for tid, p in prog.items():
        head = f"{TRACK_NAMES.get(tid, tid)} {tid}{p['level']} {p['name']}".strip()
        if p["next"]:
            head += f" → {tid}{p['next']['level']}: " + "; ".join(f"{r['id']} {r['label']}" for r in p["results"])
        lines.append(head)
    lv = gdb.levels(uid)
    tier_lv = lv.get("tier", 1)
    tiers = unlocked_tiers(uid)
    lines.append("Blocks: " + ", ".join(t["label"] for t in tiers) + (f" (tier {tier_lv})" if tier_lv > 1 else ""))
    cur, best = streak(uid, today)
    lines.append(f"Streak: {cur} days, best {best}, goal {settings.STREAK_GOAL}.")
    if target:
        lines.append("Pre-baby target: " + ", ".join(f"{t}{l}" for t, l in target.items()))
    if user["survival"]:
        lines.append("Survival mode on; levels frozen.")
    return "\n".join(lines)
