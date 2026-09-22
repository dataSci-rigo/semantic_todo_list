"""Load and validate growth/content/*.yaml once; fail loudly at startup."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import settings

STEP_TYPES = {"scale5", "yesno", "ynp", "count10", "pick", "multi", "text", "timer", "taskpick", "note"}
METRIC_KINDS = {"count", "days", "ratio", "measure"}
FREQ_PREFIXES = ("daily", "weekly:", "every_n_days:", "once")
MAX_KEY_LEN = 20
MAX_VALUE_LEN = 24

_content: "Content | None" = None


class Content:
    def __init__(self, d: dict[str, Any]):
        self.arc = d["arc"]
        self.electives = d["electives"]
        self.tracks = d["tracks"]
        self.tiers = d["tiers"]
        self.excuses = d["excuses"]
        self.rotations = d["rotations"]
        self.observation = d["observation"]
        self.visualization = d["visualization"]
        self.copy = d["copy"]
        self.touches: dict[str, dict] = {t["id"]: t for t in self.arc["touches"]}
        self.modules: dict[str, dict] = {m["id"]: m for m in self.electives["modules"]}
        self.track_defs: dict[str, dict] = {t["id"]: t for t in self.tracks["tracks"]}
        self.tier_defs: list[dict] = self.tiers["tiers"]

    def touch(self, kind: str) -> dict:
        return self.touches[kind]

    def modules_for_slot(self, kind: str, slot_id: str) -> list[dict]:
        key = f"{kind}.{slot_id}"
        return [m for m in self.electives["modules"] if m.get("slot") == key]

    def tier(self, tier_id: str) -> dict:
        for t in self.tier_defs:
            if t["id"] == tier_id:
                return t
        return self.tier_defs[0]

    def obs_week(self, n: int) -> dict:
        weeks = self.observation["weeks"]
        for w in weeks:
            if w["week"] == n:
                return w
        return weeks[0]


def _read(name: str) -> dict:
    path: Path = settings.CONTENT_DIR / f"{name}.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def _check_step(where: str, step: dict, prefix: str = "") -> None:
    if "slot" in step:
        return
    sid = step.get("id")
    if not sid:
        raise ValueError(f"{where}: step without id: {step}")
    key = f"{prefix}{sid}"
    if len(key) > MAX_KEY_LEN:
        raise ValueError(f"{where}: step key too long: {key}")
    t = step.get("type", "note")
    if t not in STEP_TYPES:
        raise ValueError(f"{where}: unknown step type {t!r} in {key}")
    for opt in step.get("options", []) or []:
        if len(str(opt["value"])) > MAX_VALUE_LEN:
            raise ValueError(f"{where}: option value too long in {key}: {opt['value']}")
    for b in step.get("buttons", []) or []:
        if len(str(b["value"])) > MAX_VALUE_LEN:
            raise ValueError(f"{where}: button value too long in {key}: {b['value']}")
    if t == "timer" and not (step.get("kind") or step.get("seconds_from")):
        raise ValueError(f"{where}: timer step {key} needs kind or seconds_from")


def _validate(c: Content) -> None:
    for kind, touch in c.touches.items():
        ids = set()
        slots = set()
        for step in touch.get("steps", []):
            if "slot" in step:
                if step["slot"] in slots:
                    raise ValueError(f"arc.{kind}: duplicate slot {step['slot']}")
                slots.add(step["slot"])
                continue
            _check_step(f"arc.{kind}", step)
            if step["id"] in ids:
                raise ValueError(f"arc.{kind}: duplicate step id {step['id']}")
            ids.add(step["id"])
        surv = touch.get("survival") or {}
        for s in surv.get("steps", []):
            if s not in ids:
                raise ValueError(f"arc.{kind}: survival step {s} not in steps")
        for s in surv.get("slots", []):
            if s not in slots:
                raise ValueError(f"arc.{kind}: survival slot {s} not in slots")
    for mid, m in c.modules.items():
        slot = m.get("slot", "")
        kind, _, slot_id = slot.partition(".")
        if kind not in c.touches:
            raise ValueError(f"electives.{mid}: slot touch {kind!r} unknown")
        slot_ids = {s["slot"] for s in c.touches[kind].get("steps", []) if "slot" in s}
        if slot_id not in slot_ids:
            raise ValueError(f"electives.{mid}: slot {slot!r} not declared in arc.{kind}")
        freq = str(m.get("frequency", "daily"))
        if not freq.startswith(FREQ_PREFIXES):
            raise ValueError(f"electives.{mid}: bad frequency {freq!r}")
        for step in m.get("steps", []):
            _check_step(f"electives.{mid}", step, prefix=f"{mid}.")
    for tid, track in c.track_defs.items():
        for lvl in track.get("levels", []):
            for metric in lvl.get("metrics", []):
                if metric.get("kind") not in METRIC_KINDS:
                    raise ValueError(f"tracks.{tid}: metric kind {metric.get('kind')!r} unknown")
    for tier in c.tier_defs:
        for metric in tier.get("unlock", []):
            if metric.get("kind") not in METRIC_KINDS:
                raise ValueError(f"tiers.{tier['id']}: metric kind {metric.get('kind')!r} unknown")
    if len(c.observation["weeks"]) < 1:
        raise ValueError("observation.yaml: no weeks")
    for seed in c.excuses["seeds"]:
        if seed["family"] not in (1, 2):
            raise ValueError(f"excuses: bad family in {seed['id']}")


def load(force: bool = False) -> Content:
    global _content
    if _content is None or force:
        c = Content({name: _read(name) for name in
                     ("arc", "electives", "tracks", "tiers", "excuses", "rotations",
                      "observation", "visualization", "copy")})
        _validate(c)
        _content = c
    return _content


def content() -> Content:
    return load()
