"""Phase 1b: context-query engine. Matches open tasks against a stated
availability window — active time must fit, requirements (currently: supply
only — skill/condition/location requirement *types* exist in the schema but
no capture flow ever populates them, so eligibility can't check those yet)
must be satisfied, and finish-to-start dependencies must already be done."""
from __future__ import annotations

import db


def _requirements_satisfied(task_id: int) -> bool:
    reqs = db.get_requirements_for_task(task_id)
    return all(r["satisfied"] for r in reqs)


def _dependencies_met(task_id: int) -> bool:
    for dep in db.get_dependencies_for_task(task_id):
        if dep["type"] == "finish-to-start" and dep["depends_on_task_id"]:
            other = db.get_task(dep["depends_on_task_id"])
            if other is None or other["status"] != "done":
                return False
    return True


def eligible_tasks(available_minutes: int, location: str | None = None) -> list:
    """location is accepted for display purposes only — no capture flow
    currently populates location-type requirements, so it isn't filtered on."""
    eligible = []
    for row in db.get_all_tasks():
        if row["estimated_active_minutes"] is None:
            continue
        if row["estimated_active_minutes"] > available_minutes:
            continue
        if not _requirements_satisfied(row["id"]):
            continue
        if not _dependencies_met(row["id"]):
            continue
        eligible.append(row)
    return eligible
