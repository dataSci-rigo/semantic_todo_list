"""Type-driven step machinery: conditions, templating, expansion, rendering.

A touch definition (arc.yaml) is expanded at open time into a concrete list
of step dicts (electives inserted at their slots, repeat_for steps
multiplied); the list is stored in the touch's state so a running card never
depends on a YAML edit. Rendering is by `type` only."""
from __future__ import annotations

import copy as _copy
import re
from typing import Any

from . import render, settings
from .render import esc

_TEMPLATE_RE = re.compile(r"\{(ctx|answers|core|item)\.([A-Za-z0-9_.]+)\}")


# ── scope helpers ─────────────────────────────────────────────────────────

def lookup(scope: dict, path: str) -> Any:
    """Resolve 'answers.three_good.good_1' or 'ctx.p1_title' against scope."""
    root, _, rest = path.partition(".")
    cur: Any = scope.get(root)
    if rest:
        # answers may be keyed by dotted elective field names — try the full key first
        if isinstance(cur, dict) and rest in cur:
            return cur[rest]
        for part in rest.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part)
            else:
                return None
    return cur


def fill(text: str | None, scope: dict, escape: bool = True) -> str:
    if not text:
        return ""

    def sub(m: re.Match) -> str:
        val = lookup(scope, f"{m.group(1)}.{m.group(2)}")
        if val is None:
            return ""
        if isinstance(val, list):
            val = ", ".join(str(v) for v in val)
        return esc(val) if escape else str(val)

    return _TEMPLATE_RE.sub(sub, text)


def evaluate(cond: Any, scope: dict) -> bool:
    """Declarative conditions; no eval."""
    if cond is None:
        return True
    if isinstance(cond, bool):
        return cond
    if not isinstance(cond, dict):
        return bool(cond)
    if "all" in cond:
        return all(evaluate(c, scope) for c in cond["all"])
    if "any" in cond:
        return any(evaluate(c, scope) for c in cond["any"])
    if "not" in cond:
        return not evaluate(cond["not"], scope)
    val = lookup(scope, cond["path"])
    if "eq" in cond:
        return _cmp_eq(val, cond["eq"])
    if "ne" in cond:
        return not _cmp_eq(val, cond["ne"])
    if "in" in cond:
        return any(_cmp_eq(val, v) for v in cond["in"])
    if "not_in" in cond:
        return not any(_cmp_eq(val, v) for v in cond["not_in"])
    if "gt" in cond:
        return _num(val) is not None and _num(val) > float(cond["gt"])
    if "gte" in cond:
        return _num(val) is not None and _num(val) >= float(cond["gte"])
    if "lt" in cond:
        return _num(val) is not None and _num(val) < float(cond["lt"])
    if "lte" in cond:
        return _num(val) is not None and _num(val) <= float(cond["lte"])
    if "truthy" in cond:
        return bool(val) == bool(cond["truthy"])
    return bool(val)


def _cmp_eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    return str(a) == str(b)


def _num(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── expansion ─────────────────────────────────────────────────────────────

def expand(touch_def: dict, slot_modules: dict[str, list[dict]], survival: bool,
           repeat_sources: dict[str, list[dict]], levels: dict[str, int]) -> list[dict]:
    """Concrete step list for one touch instance. Each step gets `key` (used
    in callback data and answers) and, for elective steps, `module_id`."""
    surv = touch_def.get("survival") or {}
    surv_steps = set(surv.get("steps", []))
    surv_slots = set(surv.get("slots", []))
    out: list[dict] = []
    for raw in touch_def.get("steps", []):
        if "slot" in raw:
            if survival and raw["slot"] not in surv_slots:
                continue
            for module in slot_modules.get(raw["slot"], []):
                for ms in module.get("steps", []):
                    step = _copy.deepcopy(ms)
                    step["base_field"] = ms.get("field", ms["id"])
                    step["key"] = f"{module['id']}.{step['id']}"
                    step["field"] = f"{module['id']}.{step.get('field', step['id'])}"
                    step["module_id"] = module["id"]
                    _fix_next(step, module["id"])
                    out.append(step)
            continue
        if survival and raw["id"] not in surv_steps:
            continue
        if survival and raw.get("survival") is False:
            continue
        sul = raw.get("skip_until_level")
        step = _copy.deepcopy(raw)
        step.setdefault("field", step["id"])
        step["base_field"] = step["field"]
        step["key"] = step["id"]
        if sul:
            # below the level the step is skippable; at/above it is required
            required = all(levels.get(track, 1) >= lvl for track, lvl in sul.items())
            step["skip"] = not required
        if step.get("repeat_for"):
            items = repeat_sources.get(step["repeat_for"], [])
            for item in items:
                s = _copy.deepcopy(step)
                s["key"] = f"{step['id']}_{item['id']}"
                s["field"] = f"{step['field']}_{item['id']}"
                s["item"] = item
                s["prompt"] = fill(step.get("prompt"), {"item": item}, escape=False)
                out.append(s)
            continue
        out.append(step)
    return out


def _fix_next(step: dict, module_id: str) -> None:
    for b in step.get("buttons", []) or []:
        nxt = b.get("next")
        if nxt and "." not in nxt:
            b["next"] = f"{module_id}.{nxt}"
    if step.get("after") and "." not in step["after"]:
        step["after"] = f"{module_id}.{step['after']}"


def eligible(step: dict, scope: dict) -> bool:
    return evaluate(step.get("when"), scope)


# ── rendering ─────────────────────────────────────────────────────────────

def _close(touch_id: int) -> tuple[str, str]:
    return ("Close", f"gb:{touch_id}:x:1")


def _cb(touch_id: int, key: str, value: Any) -> str:
    return f"gb:{touch_id}:{key}:{value}"


def render_step(touch_id: int, step: dict, scope: dict, options: list[dict] | None,
                prompt_text: str, k: int, n: int, title: str, timer_line: str | None = None,
                selected: list | None = None) -> tuple[str, dict | None]:
    """Returns (html text, keyboard). `options` are resolved by the caller for
    pick/multi/taskpick; `prompt_text` is already templated (unescaped)."""
    t = step.get("type", "note")
    rows: list[list[tuple[str, str]]] = []
    footer = None
    body = esc(prompt_text) if prompt_text else ""
    if t == "scale5":
        rows.append([(str(i), _cb(touch_id, step["key"], i)) for i in range(1, 6)])
    elif t == "yesno":
        rows.append([("Yes", _cb(touch_id, step["key"], "y")), ("No", _cb(touch_id, step["key"], "n"))])
    elif t == "ynp":
        rows.append([("Yes", _cb(touch_id, step["key"], "y")), ("Partial", _cb(touch_id, step["key"], "p")),
                     ("No", _cb(touch_id, step["key"], "n"))])
    elif t == "count10":
        rows.append([(str(i), _cb(touch_id, step["key"], i)) for i in range(0, 6)])
        rows.append([(str(i), _cb(touch_id, step["key"], i)) for i in range(6, 11)])
    elif t in ("pick", "taskpick"):
        rows.extend(_option_rows(touch_id, step["key"], options or []))
    elif t == "multi":
        sel = {str(v) for v in (selected or [])}
        opts = []
        for o in options or []:
            mark = "✓ " if str(o["value"]) in sel else ""
            opts.append({"value": o["value"], "label": mark + o["label"]})
        rows.extend(_option_rows(touch_id, step["key"], opts))
        rows.append([("Done", _cb(touch_id, step["key"], "ok"))])
    elif t == "text":
        footer = "Reply to this message, or type. /skip passes." if step.get("skip", True) \
            else "Reply to this message, or type."
        if step.get("skip", True):
            rows.append([("Skip", _cb(touch_id, step["key"], "skip"))])
    elif t == "timer":
        if timer_line:
            body = (body + "\n" if body else "") + esc(timer_line)
        rows.append([("Stop", _cb(touch_id, step["key"], "stop"))])
    elif t == "note":
        buttons = step.get("buttons")
        if buttons:
            rows.append([(b["label"], _cb(touch_id, step["key"], b["value"])) for b in buttons])
        elif not step.get("end"):
            rows.append([("Next", _cb(touch_id, step["key"], "ok"))])
    has_close = any(str(b.get("label", "")).lower() == "close" for b in (step.get("buttons") or []))
    if not step.get("end") and not has_close:
        rows.append([_close(touch_id)])
    text = render.card(title, k, n, body, footer)
    return text, (render.kb(rows) if rows else None)


def _option_rows(touch_id: int, key: str, options: list[dict]) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    short = all(len(str(o["label"])) <= 12 for o in options) and len(options) <= 6
    if short:
        row: list[tuple[str, str]] = []
        for o in options:
            row.append((str(o["label"]), _cb(touch_id, key, o["value"])))
            if len(row) == 3:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    else:
        for o in options:
            rows.append([(str(o["label"])[:48], _cb(touch_id, key, o["value"]))])
    return rows


def parse_value(step: dict, raw: str, options: list[dict] | None) -> Any:
    """Turn callback text into the stored value; None if it doesn't match."""
    t = step.get("type", "note")
    if t in ("scale5", "count10"):
        return int(raw) if raw.isdigit() else None
    if t == "yesno":
        return {"y": "yes", "n": "no"}.get(raw)
    if t == "ynp":
        return {"y": "yes", "p": "partial", "n": "no"}.get(raw)
    if t in ("pick", "taskpick", "multi"):
        for o in options or []:
            if str(o["value"]) == raw:
                return o["value"]
        return None
    if t == "note":
        for b in step.get("buttons", []) or []:
            if str(b["value"]) == raw:
                return b["value"]
        return "seen" if raw == "ok" else None
    if t == "timer":
        return raw if raw in ("stop", "done") else None
    return raw
