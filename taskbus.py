"""TaskBus v1 — the shared Google-Tasks format that lets multiple bots
co-manage one task list. This module is the reference implementation:
another bot can copy this single file (stdlib-only) and interoperate.

The contract (full spec with examples: TASKBUS.md):

- All bots share ONE Google Tasks list (default name "TaskBus").
- Native Google fields stay native: title, status, parent (hierarchy),
  completed/due. Bots' structured data rides in the task's `notes`:

      <free-form human notes — anything, may be empty>
      TB1 {"app":"stm","v":3,"base":2,"lock":false,"f":{"stm":{...}}}

  The LAST line starting with "TB1 " is the machine block; everything above
  it is human notes. A task without a TB1 line is still valid (a human or a
  non-TaskBus app made it) — treat it as v=0, adopt it on first write.

- Versioning: `v` increments by exactly 1 on every write by any bot.
  `base` is the `v` the writer read before editing (0 when creating).
  Each bot remembers the last `v` it saw per task; on pull:
    * remote v == last seen           -> nothing new
    * remote v >  last seen, no local edits -> accept remote wholesale
    * remote v >  last seen, local edits    -> both diverged: merge, then
      write the merged task with base = remote v, v = remote v + 1
- `lock`: set true (with your app id in `app`) to forbid other apps from
  modifying the task. A locked task may only be written by the app that
  holds the lock. If another app overwrote a lock anyway, the lock holder
  restores its copy on next sync.
- `f` is a per-app namespace dict: {"stm": {...}, "yourbot": {...}}.
  Write ONLY your own namespace; carry every other key through verbatim,
  byte-for-byte semantics ("copy over whatever the other bot wrote as is").
  Only AI calls should ever interpret a foreign namespace.
- Reserved for future use: "identity" (cross-project shared-subtask key).
"""
from __future__ import annotations

import json
import re

SENTINEL = "TB1 "
_TB_RE = re.compile(r"^TB1 (\{.*\})\s*$")

PROTOCOL_KEYS = ("app", "v", "base", "lock", "f", "identity")


def parse_notes(notes: str | None) -> tuple[str, dict | None]:
    """Split a task's notes into (human_text, block). block is None when the
    task carries no TB1 line."""
    if not notes:
        return "", None
    lines = notes.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        m = _TB_RE.match(lines[i])
        if m:
            try:
                block = json.loads(m.group(1))
            except ValueError:
                break
            human = "\n".join(lines[:i]).rstrip()
            return human, block
    return notes.rstrip(), None


def build_notes(human_text: str, block: dict) -> str:
    """Compose the notes field from human text + machine block."""
    human = (human_text or "").rstrip()
    line = SENTINEL + json.dumps(block, separators=(",", ":"), ensure_ascii=False)
    return f"{human}\n\n{line}" if human else line


def make_block(app: str, version: int, base: int, own_fields: dict,
               foreign: dict | None = None, lock: bool = False,
               identity: str | None = None) -> dict:
    """Build a block for writing. `foreign` is the previous block's `f` dict;
    every namespace except `app`'s is carried through verbatim."""
    f = dict(foreign or {})
    f[app] = own_fields
    block = {"app": app, "v": version, "base": base, "lock": lock, "f": f}
    if identity:
        block["identity"] = identity
    return block


def next_write(prev_block: dict | None, app: str, own_fields: dict,
               lock: bool = False) -> dict:
    """Standard write: bump v by 1 over what was read, preserve foreign
    namespaces and unknown top-level keys."""
    prev_v = int(prev_block.get("v", 0)) if prev_block else 0
    block = make_block(app, prev_v + 1, prev_v, own_fields,
                        foreign=(prev_block or {}).get("f"), lock=lock,
                        identity=(prev_block or {}).get("identity"))
    if prev_block:
        for k, val in prev_block.items():
            if k not in PROTOCOL_KEYS:
                block[k] = val  # forward-compat: carry unknown keys through
    return block


def is_locked_against(block: dict | None, app: str) -> bool:
    """True if this task is locked by a different app."""
    return bool(block and block.get("lock") and block.get("app") != app)


def classify_remote_change(remote_v: int, last_seen_v: int,
                            local_dirty: bool) -> str:
    """The sync decision, exactly as specified:
    'unchanged'  — remote didn't move
    'accept'     — remote moved, we didn't: take theirs wholesale
    'merge'      — both moved from the same base: field-wise merge
    """
    if remote_v <= last_seen_v:
        return "unchanged"
    return "merge" if local_dirty else "accept"
