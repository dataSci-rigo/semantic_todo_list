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
