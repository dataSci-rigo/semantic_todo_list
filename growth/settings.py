"""Growth Bot settings: STM's config plus optional GROWTH_* overrides.

No new .env keys are required. GROWTH_CHAT_ID / GROWTH_THREAD_ID move the
touches to another chat or forum topic; GROWTH_ALLOWED_USER_IDS widens who
may use growth commands (default: the STM owner)."""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import config as stm  # STM's top-level config module (not this package's)

TZ_NAME = stm.TIMEZONE
TZ = ZoneInfo(TZ_NAME)

PACKAGE_DIR = Path(__file__).parent
CONTENT_DIR = PACKAGE_DIR / "content"
DATA_DIR = Path(stm.DB_PATH).parent
FEEDBACK_PATH = DATA_DIR / "growth_feedback.md"

OWNER_USER_ID = stm.CHAT_ID                     # a DM chat id == the user's id
CHAT_ID = int(os.getenv("GROWTH_CHAT_ID", "0") or 0) or stm.CHAT_ID
THREAD_ID = int(os.getenv("GROWTH_THREAD_ID", "0") or 0) or None
_allowed = {int(x) for x in os.getenv("GROWTH_ALLOWED_USER_IDS", "").split(",") if x.strip().lstrip("-").isdigit()}
ALLOWED_USER_IDS = _allowed or {OWNER_USER_ID}

DEFAULT_MORNING_TIME = "07:30"
DEFAULT_EVENING_TIME = "21:00"
DEFAULT_REFOCUS_WINDOWS = ["10:30", "13:30", "16:30"]
REFOCUS_JITTER_MIN = 20
REFOCUS_MIN_SPACING_MIN = 45

# How late a scheduled touch may still fire; beyond this it is marked expired.
GRACE = {
    "morning": timedelta(hours=3),
    "evening": timedelta(hours=3),
    "refocus": timedelta(minutes=30),
}
# How long an active card accepts answers after it fired.
ANSWER_WINDOW = {
    "morning": timedelta(hours=12),
    "evening": timedelta(hours=6),
    "refocus": timedelta(minutes=30),
    "default": timedelta(hours=3),
}
TIMER_LATE_EXPIRE = timedelta(minutes=5)     # a timer overdue by more (restart) is expired, not fired
PROMPT_EXPIRY = timedelta(hours=3)
TIMER_SECONDS = {"surf": 90, "start5": 300}
NIGHTLY_HOUR = 3
MAX_FIRES_PER_TICK = 2
BUSY_HORIZON_SECONDS = 90

MODEL_FAST = stm.MODEL_HAIKU
MODEL_SMART = stm.MODEL_SONNET
AI_TIMEOUT = 30.0

STREAK_GOAL = 30
STREAK_MILESTONES = (7, 14, 30)
CALLBACK_MAX_BYTES = 64
