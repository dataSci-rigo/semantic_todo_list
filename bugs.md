Summary of Key Issues Found:
Invalid Anthropic Model Identifiers: config.py uses invalid model strings (claude-haiku-4-5-20251001 and claude-sonnet-5), which fail API calls with a 404 error.
Procedures Excluded from Availability Matching: Photo recipes and instructions never populate estimated_active_minutes, permanently excluding them from /now and /window availability context queries.
Wrong Task Deletion in Correction Loop: Natural-language correction replies often mix up 1-based display position indices (e.g. delete item 2) with database task IDs, potentially deleting or updating unrelated tasks.
Unhandled Exception Risks: Unhandled TypeError / JSONDecodeError / ValueError scenarios in AI extraction, missing database rows, string formatting, and callback parsing.
Database & SQL Bugs: Duplicate shopping list entries when task_id is None and SQL syntax errors on empty exclude_status tuples.


Key Additions to the Plan:
Eliminate Notification Spam during Ingest: Instead of flooding Telegram with 15+ inline classification messages at once when uploading multi-task captures/screenshots, tasks will be presented in a clean summary with single-click batching or lazy classification via /unclassified.
Interactive Shopping Lists (/store, /online, /groceries): Added real-time inline checkoff buttons so items can be marked as purchased directly while shopping at the store.
Actionable Task Query Buttons (/now, /window): Added inline action buttons ([✓ Done #ID]) directly to availability window queries for single-tap completion.
Procedure Duration Calculation: Automated active/passive duration calculation for recipes/instructions so they seamlessly integrate with context matching.
Please let me know if you approve this plan and would like me to begin implementing these changes!
