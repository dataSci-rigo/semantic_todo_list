# Semantic Task Manager — Comprehensive Bug Fixes & UX Enhancements Plan

This document outlines a phased plan to resolve critical bugs, optimize the ingestion pipeline, and overhaul the user experience (UX) to eliminate message spam and friction for the Semantic Task Manager system.

---

## 1. Bug Fixes & Technical Debt (Phase 1)

### 1.1 Anthropic API Model Constants
> [!WARNING]
> Calls to Anthropic API currently fail with `404 / NotFoundError` because model identifiers are invalid.

- **Location:** [config.py](file:///home/ai1/Documents/semantic_task_manager/config.py#L15-L16)
- **Fix:** Update model constants:
  ```python
  MODEL_HAIKU  = "claude-3-5-haiku-latest"
  MODEL_SONNET = "claude-3-5-sonnet-latest"
  ```

### 1.2 Procedure/Recipe Active & Passive Time Aggregation
> [!IMPORTANT]
> Procedure tasks (recipes and instructions) are missing estimated durations and are completely ignored by context matching (`/now` and `/window`).

- **Location:** [bot.py](file:///home/ai1/Documents/semantic_task_manager/bot.py#L90-L101), [flows.py](file:///home/ai1/Documents/semantic_task_manager/flows.py#L242-L260)
- **Fix:** When saving a procedure task, calculate total `active_minutes` and `passive_minutes` across steps/ingredients and populate `tasks.estimated_active_minutes` and `tasks.estimated_passive_minutes`.

### 1.3 Natural Language Correction Index Resolver
> [!CAUTION]
> Replies to capture messages (e.g. "delete the second one") can edit or delete unrelated database tasks because 1-based display position indices are confused with database IDs.

- **Location:** [ai.py](file:///home/ai1/Documents/semantic_task_manager/ai.py#L81-L96), [bot.py](file:///home/ai1/Documents/semantic_task_manager/bot.py#L170-L205)
- **Fix:**
  1. Pass 1-based position indices explicitly in the payload to `ai.apply_correction`.
  2. Implement an index-resolver in `handle_correction`: if the returned ID is in range `1..N` (position index) and not a valid database task ID, resolve position `k` to `task_ids[k - 1]`.

### 1.4 Robust AI JSON Parsing & Type Safety
- **Location:** [ai.py](file:///home/ai1/Documents/semantic_task_manager/ai.py#L17-L23), [ai.py](file:///home/ai1/Documents/semantic_task_manager/ai.py#L108-L111)
- **Fix:**
  - Handle `null` values safely in `classify_task` with `int(result.get(k) or 0)`.
  - Upgrade `_extract_json` to extract raw JSON substrings `{...}` or `[...]` when markdown fences are omitted by Claude.

### 1.5 Database & Exception Guard Fixes
- **Location:** [db.py](file:///home/ai1/Documents/semantic_task_manager/db.py#L180-L196), [db.py](file:///home/ai1/Documents/semantic_task_manager/db.py#L498-L509), [flows.py](file:///home/ai1/Documents/semantic_task_manager/flows.py), [telegram_api.py](file:///home/ai1/Documents/semantic_task_manager/telegram_api.py)
- **Fix:**
  - Prevent duplicate `shopping_list_items` when `task_id` is `None` by checking existence prior to insertion.
  - Omit `status NOT IN (...)` clause in `get_all_tasks` if `exclude_status` is empty.
  - Add `None` guards for task/entity DB lookups.
  - Change `if thread_id:` check to `if thread_id is not None:` in `telegram_api.py`.

---

## 2. Ingestion & UX Enhancements (Phase 2)

### 2.1 Eliminate Notification Spam / Interrogation Barrage
> [!TIP]
> Uploading a screenshot with 5 tasks currently triggers 15+ sequential bot messages in Telegram.

- **Location:** [bot.py](file:///home/ai1/Documents/semantic_task_manager/bot.py#L49-L50), [flows.py](file:///home/ai1/Documents/semantic_task_manager/flows.py#L41-L55)
- **Improvement:**
  - When multiple tasks are captured at once (from a list screenshot or multi-task text), do not automatically launch simultaneous classification keyboards for all of them.
  - Instead, display a clean capture summary message with an inline button: `[Classify Next Task]` or let users trigger classification on-demand via `/unclassified`.

### 2.2 Interactive Shopping Lists (`/store`, `/online`, `/groceries`)
- **Location:** [bot.py](file:///home/ai1/Documents/semantic_task_manager/bot.py#L377-L390), [flows.py](file:///home/ai1/Documents/semantic_task_manager/flows.py)
- **Improvement:**
  - Upgrade shopping list commands (`/store`, `/online`, `/groceries`) to attach inline keyboard checkoff buttons (`[🛒 Purchased — <Item>]`).
  - Tapping a checkoff button marks the item as purchased in `shopping_list_items`, sets `inventory.on_hand = 1`, and dynamically updates the message.

### 2.3 One-Tap Action Buttons on Task Queries (`/tasks`, `/now`, `/window`)
- **Location:** [bot.py](file:///home/ai1/Documents/semantic_task_manager/bot.py#L224-L237)
- **Improvement:**
  - Attach quick inline buttons (e.g. `[✓ Done #ID]`) to tasks listed in `/now` and `/window` outputs so users can complete tasks with a single tap while reviewing their availability window.

---

## Proposed File Changes Summary

### [Component: Config & AI Engine]
#### [MODIFY] [config.py](file:///home/ai1/Documents/semantic_task_manager/config.py)
- Fix model identifiers.

#### [MODIFY] [ai.py](file:///home/ai1/Documents/semantic_task_manager/ai.py)
- Improve JSON parsing resilience and correction index schema.

### [Component: Storage & Logic]
#### [MODIFY] [db.py](file:///home/ai1/Documents/semantic_task_manager/db.py)
- Fix NULL task ID shopping list duplicate issue and empty `exclude_status` SQL syntax bug.

#### [MODIFY] [context.py](file:///home/ai1/Documents/semantic_task_manager/context.py)
- Ensure robust matching for tasks with estimated durations.

### [Component: Ingest, Flows & UX]
#### [MODIFY] [flows.py](file:///home/ai1/Documents/semantic_task_manager/flows.py)
- Calculate procedure active/passive minutes.
- Batch classification flow to eliminate notification spam.
- Add interactive shopping checklist callbacks.

#### [MODIFY] [bot.py](file:///home/ai1/Documents/semantic_task_manager/bot.py)
- Safely resolve 1-based indices in correction handler.
- Add interactive inline buttons for `/store`, `/online`, `/groceries`, `/now`, `/window`.

#### [MODIFY] [telegram_api.py](file:///home/ai1/Documents/semantic_task_manager/telegram_api.py)
- Fix `thread_id` check.

---

## Verification Plan

### Automated Tests
1. Run Python syntax compiler check:
   ```bash
   python3 -m py_compile config.py ai.py db.py context.py flows.py bot.py telegram_api.py
   ```
2. Unit test DB edge cases:
   - Duplicate shopping list items with `task_id=None`.
   - `get_all_tasks(exclude_status=())` query execution.

### Manual Verification
- Verify that natural language corrections map 1-based position indices accurately.
- Verify procedure ingestion populates estimated active minutes.
- Test interactive shopping list checkoff buttons (`/store`, `/online`, `/groceries`).
- Confirm multi-task capture no longer floods Telegram with notification spam.
