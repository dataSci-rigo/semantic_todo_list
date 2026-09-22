"""Excuse Catalog: seeding, counter-moves, self-forgiveness rotation, reports."""
from __future__ import annotations

from . import bank, clock, gdb, loader, settings, steps


def seed_user(user_id: int) -> None:
    if gdb.excuse_seeded(user_id):
        return
    for s in loader.content().excuses["seeds"]:
        gdb.add_excuse(user_id, int(s["family"]), s["text"], s.get("size", "small"), s.get("counter_move"),
                       s.get("follow_up"), s.get("flag"), seed=s["id"])


def seed_all_users() -> None:
    for u in gdb.active_users():
        seed_user(u["user_id"])


def forgiveness_line(user_id: int) -> str:
    lines = loader.content().rotations["self_forgiveness"]
    n = len(gdb.events(user_id, "excuse_logged")) + len(gdb.events(user_id, "excuse_noted"))
    return lines[n % len(lines)]


def counter_text(user, excuse_row, sc: dict) -> str:
    if excuse_row is None:
        return "Logged. " + forgiveness_line(user["user_id"])
    move = steps.fill(excuse_row["counter_move"] or "5-min start.", sc, escape=False)
    extra = ""
    fu = excuse_row["follow_up"] or ""
    if fu.startswith("remind:"):
        row = bank.remind(user["user_id"], fu.split(":", 1)[1])
        if row:
            extra = "\n" + bank.format_line(row)
    elif fu == "drive_audit":
        why = sc["core"].get("why")
        if why:
            extra = f"\n{why}"
    elif fu == "rest_check":
        left = sc["ctx"].get("rest_left_min")
        extra = f"\nRest budget left: {left} min." if left is not None else ""
    return f"{move}{extra}\n{forgiveness_line(user['user_id'])}"


def report(user_id: int, today: str, days: int = 30) -> str:
    since = clock.day_offset(today, -days)
    logs = gdb.excuse_logs(user_id, since)
    if not logs:
        return f"No excuses logged in the last {days} days."
    by_family: dict[int, dict[str, int]] = {1: {}, 2: {}}
    by_task: dict[str, int] = {}
    big: dict[str, int] = {}
    for l in logs:
        fam = int(l["family"])
        by_family.setdefault(fam, {})[l["text"]] = by_family.setdefault(fam, {}).get(l["text"], 0) + 1
        if l["task_name"]:
            by_task[l["task_name"]] = by_task.get(l["task_name"], 0) + 1
        if l["size"] == "big":
            big[l["text"]] = big.get(l["text"], 0) + 1
    names = loader.content().excuses["families"]
    out = [f"Excuses, last {days} days: {len(logs)}."]
    for fam in (1, 2):
        items = sorted(by_family.get(fam, {}).items(), key=lambda kv: -kv[1])
        if items:
            out.append(f"{names[fam]}: " + "; ".join(f"{t} ×{n}" for t, n in items[:6]))
    if by_task:
        top = sorted(by_task.items(), key=lambda kv: -kv[1])[:3]
        out.append("By task: " + "; ".join(f"{t} ×{n}" for t, n in top))
    recurring = [t for t, n in big.items() if n >= 3]
    if recurring:
        out.append("Big excuses recurring 3+: " + "; ".join(recurring))
    return "\n".join(out)
