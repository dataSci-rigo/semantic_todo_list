import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TELEGRAM_TOKEN  = os.getenv("STM_BOT_ID", "")
CHAT_ID         = int(os.getenv("STM_CHAT_ID", "0") or "0")
IS_FORUM        = os.getenv("STM_IS_FORUM", "false").lower() == "true"
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

DB_PATH = Path(__file__).parent / "data" / "tasks.db"

MODEL_HAIKU  = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-5"

GOOGLE_CLIENT_SECRET_PATH = os.getenv(
    "GOOGLE_CLIENT_SECRET_PATH",
    str(Path.home() / "Documents" /
        "client_secret_994331043279-p0c58fgvb760e5b37p90qgp35asn4e5j.apps.googleusercontent.com.json"),
)
GOOGLE_TOKEN_PATH = Path(__file__).parent / "data" / "google_token.json"
GOOGLE_TASKS_LIST_NAME = os.getenv("TASKBUS_LIST_NAME", "TaskBus")
GOOGLE_TASKS_SCOPES = [
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
SYNC_INTERVAL_SECONDS = 10 * 60

# TaskBus protocol (see TASKBUS.md)
TASKBUS_APP_ID = "stm"
# The Google account every bot syncs the TaskBus through. Set it in .env only
# (STM_SYNC_ACCOUNT): the repo is public, so no address lives in code. Unset,
# the wrong-account check is skipped (a dead token is still caught).
SYNC_ACCOUNT = os.getenv("STM_SYNC_ACCOUNT", "").strip()
SYNC_ACCOUNT_LABEL = SYNC_ACCOUNT or "the TaskBus Google account (STM_SYNC_ACCOUNT)"

TIMEZONE = os.getenv("TIMEZONE", "America/Los_Angeles")
WINDOW_CHECK_INTERVAL_SECONDS = 60
