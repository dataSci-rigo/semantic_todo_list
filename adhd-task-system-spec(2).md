# ADHD Task Capture & Context-Aware Task Manager — Spec v0.1

Single-user system. Telegram bot on a VM, SQLite for all storage, Anthropic API for all intelligence. This document covers Phase 0, Phase 1a, and Phase 1b boundaries. Phase 2 (goal decomposition from life aspects) is out of scope here and noted only where it constrains the data model.

## Guiding principles

Capture must be nearly frictionless: any text, screenshot, or photo sent to the bot becomes a saved task or a saved draft — nothing is ever lost because a flow was abandoned. Classification is guess-and-confirm, never interrogation: the model proposes, the user taps. All free-form vocabulary (supplies, skills, conditions, locations) passes through AI normalization against a canonical entity list so the taxonomy grows organically but stays deduplicated and queryable. Expected scale: roughly 100–200 conditions, 50+ skills, ~10 locations, ~200 supplies.

## Model tiering

Two tiers of Anthropic API usage. Haiku handles the high-volume, low-stakes calls: entity normalization, classification guessing (urgency, interest, energy, value), list transcription from screenshots, and applying correction instructions. A larger model (Sonnet-class) handles the low-volume, high-value calls: interpreting a photo of a situation into candidate tasks, generating step-by-step plans calibrated to a stated skill level, and producing tool/supply requirement lists. Normalization calls always include the relevant canonical entity list in the prompt and must return entity IDs (or an explicit new-entity proposal), never free text — this is what keeps Haiku's cheapness from becoming a data-quality liability.

## Data model (SQLite)

Described structurally, not as final DDL.

**tasks** — id, title, description, parent_task_id (self-reference for subtasks), status (draft / classified / ready / blocked / done / dropped), classification_complete flag, urgency (0–5), interest (0–5), energy (0–5), value (0–5), skill_level_required (0–5, per task), estimated_active_minutes, estimated_passive_minutes (e.g., drying time), source (text / screenshot / photo), source_message_id (Telegram message id, for edit-via-reply), created_at, updated_at.

**entities** — id, type (supply / skill / condition / location), canonical_name, notes. **entity_aliases** — entity_id, alias. Together these are the normalization target. New entities are inserted only after a one-tap user confirmation.

**task_requirements** — task_id, entity_id, requirement kind matches entity type (needs supply X, needs skill Y at level N, needs condition Z, at location L), plus a satisfied flag where applicable.

**inventory** — entity_id (supplies only), on_hand (boolean or quantity), last_confirmed_at. Inventory is its own subsystem: checking off owned tools during a task flow updates it, and purchases update it.

**shopping_lists** — id, name ("supply store run", "online shopping"), and **shopping_list_items** — list_id, entity_id, task_id that triggered it, purchased flag. Marking purchased flips inventory on_hand.

**dependencies** — task_id, depends_on_task_id, type (finish-to-start for setup/cleanup ordering; passive-follow for things like paint drying that need no presence but block the area), plus free-text constraint notes captured via /dependency (e.g., "kid must be out of the house").

**availability_windows** (Phase 1b) — start, end, location, constraint notes ("kid out 4 hours"), so context queries can match window duration against task estimates.

**procedures** — id, task_id, type (recipe / instructions), title, structured content parsed at capture time: ordered steps with per-step active/passive durations where inferable, and for recipes an ingredient list with quantities. Ingredients and named tools normalize to supply entities like everything else. This table is the source of truth for future full breakdown (each step becoming a subtask); for now it is stored, not exploded.

**captures** — raw record of every inbound message (text, image file id, transcription/interpretation output, model used, cost), linked to created task ids. This is the audit trail that makes corrections and debugging possible.

## Phase 0 — Capture

Inputs: plain text message, screenshot of a list, photo of a thing/situation, or an image of a procedural document (recipe or instructions such as "install a router" / "change a filter"). One vision pipeline, model-branched: Claude classifies the image as list-like (transcribe each line into a task), situation-like (propose 3–4 candidate tasks plus "other" as Telegram inline buttons; "other" opens free-text), or procedure-like (parse into a structured procedure — see procedures table — and create a linked task). Text messages are parsed directly into one or more tasks.

Procedures are parsed and stored in full at capture time even though their richer workflows land in later phases: the structured ingredients/steps are cheap to keep now and mean future features never require re-capturing the source image.

Correction loop: replying to the bot's capture message with natural-language instructions ("second one is 'call dentist', delete the third, merge 1 and 4") triggers a Haiku call that applies the edits to the linked tasks. No command syntax required. A /delete equivalent exists but reply-editing is the primary path.

Done criteria for Phase 0: send any of the three input types, get correctly saved tasks in SQLite, correct them by reply. Nothing else.

## Phase 1a — Classify, requirements, inventory

On capture (or lazily, on demand), Haiku guesses urgency, interest, energy, and value; the bot presents them as prefilled inline buttons for one-tap confirm or adjust. Abandoned flows save with classification_complete = false; "show unclassified" surfaces them later as a low-energy tidying activity.

For actionable tasks, the bot asks skill level (0–5, per task, calibrated to "amateur — instructions suffice, no study required") and requests a plan plus tool/supply list from the larger model. Supplies and tools are normalized to entities, presented as a checklist of what the user owns; checked items update inventory, unchecked items land on both the supply-store and online shopping lists automatically. Confidence rule: when normalization confidence is low or no entity matches, the bot proposes creating a new entity and waits for a tap before inserting.

Recipe flow: when a capture parses as a recipe, the ingredient list becomes a checklist matched against inventory. For each missing ingredient, the model proposes exactly one substitution — given the full recipe as context so the substitution makes culinary sense, and given current inventory so it prefers substitutes actually on hand — presented as one message per missing ingredient with buttons: [use substitution] / [Buy]. Tapping Buy adds the original ingredient to the grocery list (a shopping_lists row alongside the supply-store and online lists). Separately, the model computes the recipe's total active and passive time (prep and hands-on cooking vs. baking, marinating, resting) and writes them to the task's estimated_active_minutes and estimated_passive_minutes, which makes recipes first-class citizens of the Phase 1b window-matching engine ("kid naps for 90 minutes — what can I cook?").

Instruction-type procedures (router install, filter change) get lighter treatment for now: stored, linked to a task, tool/supply requirements extracted and normalized, but not broken into subtasks. Full step-by-step breakout is deferred; volume is expected to be low.

Done criteria for Phase 1a: the full sink-caulking flow works end to end — photo → candidate tasks → urgency → skill → plan → supply checklist → caulk appears on both shopping lists — and "I'm going to Home Depot" returns the store list.

## Phase 1b — Context query engine

The "what can I do right now" layer. User states an availability window or asks a filtered question ("non-urgent tasks for Saturday morning"); the engine matches tasks whose requirements are fully satisfied (skills adequate, supplies in inventory, location reachable, conditions and dependency constraints met) and whose active + passive time fits the window. /dependency attaches constraints to tasks; windows recorded in advance ("Sunday, kid out 4 hours") are matched against estimates to surface eligible projects, distinguishing active time from passive/blocking time (30 min work + 2 hr drying that blocks the bathroom).

Details deliberately deferred to 1b planning: ranking among eligible tasks, recurring windows, notification/nudge behavior, and how passive-blocking tasks reserve a location.

## Non-goals (for now)

Phase 2 goal/aspect decomposition (the schema anticipates it via parent_task_id and a future goals table, but nothing more), multi-user support, calendar integration, and any local OCR engine — vision API is the pipeline; Tesseract is a cost optimization to revisit only if screenshot volume makes it worthwhile.

Also deferred: exploding stored procedures into subtasks (recipes and instructions are captured structurally now, broken out later), and any recipe library/browsing features beyond capture and cook.

## Open questions

Where the Haiku confidence threshold for auto-accepting a normalization match should sit before demanding a confirm tap (currently: always batch-confirm any new entity, never auto-accept — see implementation notes); and whether shopping lists should ever auto-clear purchased items or keep history. Neither blocks Phase 1b.

Resolved during implementation: interest/energy/value guesses happen at capture time for every task (not a nightly batch), via inline buttons the user can tap to adjust — settled in favor of capture-time for all tasks, including recipe/instruction-sourced ones. Consumable tracking stays boolean on_hand, as originally proposed: no quantity system; the user tells the system when they don't have an item (via the supply checklist, or the recipe substitution flow's "Buy" button) and it's added to the shopping/grocery list from there.
