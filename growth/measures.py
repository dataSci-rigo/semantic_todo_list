"""Named measures used by tracks.yaml metrics of kind `measure`."""
from __future__ import annotations

from . import gdb


def misalloc_minutes_per_day(user_id: int, days: list[str]) -> tuple[float | None, int]:
    """(mean minutes/day over days that have refocus data, number of days measured).
    A refocus answer of P3+/Other, or rest drifted, while a P1 exists adds
    min(minutes since the previous state event, 180)."""
    if not days:
        return None, 0
    logs = gdb.refocus_logs(user_id, days)
    per_day: dict[str, float] = {}
    measured: set[str] = set()
    for r in logs:
        measured.add(r["day"])
        mis = r["on_kind"] == "other" or (r["on_p"] or 0) >= 3 or r["rest_mode"] == "drifted"
        if mis and r["p1_title"]:
            per_day[r["day"]] = per_day.get(r["day"], 0.0) + float(min(r["minutes_since_prev"] or 30, 180))
    if not measured:
        return None, 0
    total = sum(per_day.get(d, 0.0) for d in measured)
    return total / len(measured), len(measured)


def estimate_error(user_id: int, last_n: int) -> tuple[float | None, int]:
    rows = [r for r in gdb.recent_ended_challenges(user_id, last_n * 2) if r["est_min"] and r["actual_min"]][:last_n]
    if not rows:
        return None, 0
    errs = [abs(r["actual_min"] - r["est_min"]) / float(r["est_min"]) for r in rows]
    return sum(errs) / len(errs), len(errs)


def explanatory_trend(user_id: int, last_n: int) -> tuple[float | None, int]:
    rows = gdb.graded_entries(user_id, last_n)
    styles = [(gdb.loads(r["rubric_json"], {}) or {}).get("explanatory_style") for r in rows]
    styles = [s for s in styles if s in ("optimistic", "pessimistic", "mixed")]
    if not styles:
        return None, 0
    return sum(1 for s in styles if s == "optimistic") / len(styles), len(styles)


MEASURES = {
    "misalloc_minutes_per_day": lambda uid, m, days: misalloc_minutes_per_day(uid, days),
    "estimate_error": lambda uid, m, days: estimate_error(uid, int(m.get("last_n", 5))),
    "explanatory_trend": lambda uid, m, days: explanatory_trend(uid, int(m.get("last_n", 20))),
}
