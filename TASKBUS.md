# TaskBus v1 — shared task format for co-managing bots

One Google Tasks list is the bus. Any bot (or human, via the normal Google
Tasks apps) reads and writes it; this document is the complete contract.
Reference implementation: [`taskbus.py`](taskbus.py) — stdlib-only, copy it
into your bot. The sync loop that honors this contract end-to-end is
[`sync.py`](sync.py); a standalone driver is [`sync_tasks.py`](sync_tasks.py).

- **Sync account:** the Google account set as `STM_SYNC_ACCOUNT` in `.env`
  (every bot authorizes Google Tasks OAuth as this account; `sync_tasks.py`
  refuses any other).
- **Bus list:** the Google Tasks list named **`TaskBus`** (create it if
  missing — `tasklists.list`, match by title, else `tasklists.insert`).
- **Scope needed:** `https://www.googleapis.com/auth/tasks` (plus
  `userinfo.email` if you want to verify the account).

## 1. Where data lives

Native Google Tasks fields stay native — never duplicate them:

| what | where |
|---|---|
| title | task `title` |
| open/done | task `status` (`needsAction` / `completed`) |
| project hierarchy | task `parent` (projects are top-level tasks; subtasks are children) |
| human notes + machine block | task `notes` (format below) |

## 2. The notes format

Free-form human text first (optional), then a blank line, then the machine
block: **the last line starting with `TB1 ` followed by one JSON object.**

Real payload produced by this bot (verbatim):

```
hinge is rusted

TB1 {"app":"stm","v":1,"base":0,"lock":false,"f":{"stm":{"cat":"Casa","uiev":[3,2,2,4],"pri":5}}}
```

Parsing rule: scan lines bottom-up for `^TB1 (\{.*\})$`; everything above it
(minus trailing blank lines) is the human notes. A task with **no** TB1 line
is legitimate — a human or non-TaskBus app made it. Treat it as version 0
and adopt it (add a block) on your first write.

### Block fields

| key | meaning |
|---|---|
| `app` | id of the bot that wrote this version (`"stm"` is this bot) |
| `v` | version counter — **increments by exactly 1 on every write, by any bot** |
| `base` | the `v` the writer had read before editing (`0` when creating) |
| `lock` | `true` = only the app named in `app` may modify this task |
| `f` | per-app namespaces: `{"stm": {...}, "yourbot": {...}}` |
| `identity` | *reserved* (future: cross-project shared-subtask key) — carry through if present |

Unknown top-level keys: **carry them through unchanged** (forward
compatibility). `taskbus.next_write()` does all of this for you.

### The namespace rule (the "copy as-is" rule)

Write **only your own** key inside `f`. Every other namespace is copied
through byte-for-byte on your writes — you never interpret, validate, or
prune it. Only AI calls may *read* a foreign namespace (Haiku understands
it; your code doesn't have to). Verified behavior: after `stm` and a
hypothetical `keepbot` alternate writes, both namespaces persist intact:

```
TB1 {"app":"stm","v":4,"base":3,"lock":false,"f":{"stm":{"cat":"Casa","uiev":[3,2,2,4],"pri":5},"keepbot":{"tag":"weekend"}}}
```

### `stm` namespace (this bot's fields, for the curious)

`cat` category string · `uiev` `[urgency, interest, energy, value]` 0–5 ·
`pri` absolute priority (1 = highest) · `est` `[active_min, passive_min]`.

## 3. Writing

Every write (create or update) must:

1. Read the task; parse its block (`taskbus.parse_notes`).
2. Refuse if `lock` is true and `app` isn't you.
3. Build the new block with `v = read_v + 1`, `base = read_v`, your
   namespace updated, everything else carried through
   (`taskbus.next_write`).
4. Patch `notes` (and any native fields) in one `tasks.patch`.

## 4. Syncing — accept vs merge

Each bot remembers, per task, the last `v` it saw (this bot stores it in
`google_task_links.version`). On each pull:

| remote state | local edits since last sync? | action |
|---|---|---|
| `v` unchanged and `updated` unchanged | — | nothing |
| moved (either) | no | **accept** remote wholesale; remember new `v` |
| moved | yes | **merge** (below), then write merged with `v = remote_v + 1` |

“Moved” includes **human edits from the Google Tasks UI**, which don't bump
`v` — detect them by the task's `updated` timestamp advancing past what you
stored.

Merge policy (what this bot does; match it or do better):
- `title` / human notes: if identical, keep; else AI-merge both sides
  (keep information over dropping it).
- `status`: **done wins** — if either side completed it, it's completed.
- `f`: your namespace = your side; foreign namespaces = remote side.
- Notify the human that a merge happened, with a revert affordance.

Locked tasks: if you find your locked task modified by someone else,
restore your copy (write it back with `v = their_v + 1`, lock intact) and
tell the human. Verified: a rogue overwrite of a locked task is reverted on
the next cycle.

## 5. Hierarchy

Projects are top-level tasks; their subtasks are children (`parent` on
insert). Push parents before children so the parent's Google id exists.
Completion of a parent does not auto-complete children (and vice versa) in
v1. Cross-project shared subtasks (`identity`) are reserved for v2.

## 6. Worked sequence (all verified by test)

1. `stm` creates “Fix the gate” → `v:1, base:0`.
2. `keepbot` reads it, adds its namespace, edits the title → `v:2, base:1`.
   `stm` pulls: no local edits → **accepts**, now shows keepbot's title.
3. Both edit from v2: `keepbot` writes `v:3`; `stm` has local edits and
   pulls → **merge**: Haiku combines texts, result written as `v:4,
   base:3`, keepbot's namespace untouched, human notified with revert.
4. Human checks the task off in the Google Tasks app (no block change) →
   every bot marks it done via the `updated`-timestamp rule.

## 7. Checklist for a new bot

- [ ] OAuth as the `STM_SYNC_ACCOUNT` account, scope `auth/tasks`
- [ ] Find-or-create list `TaskBus`
- [ ] Copy `taskbus.py` (or reimplement §2–§4 exactly)
- [ ] Pick an `app` id nobody uses (lowercase, short)
- [ ] Store per-task `last seen v` + the foreign `f` namespaces verbatim
- [ ] Never write over `lock`, never drop a namespace, never skip a `v` bump
