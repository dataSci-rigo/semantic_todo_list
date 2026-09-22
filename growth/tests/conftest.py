"""Test harness: scratch DB, fake Telegram, no Anthropic calls, movable clock."""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

import pytest

STM_DIR = pathlib.Path(__file__).resolve().parents[2]
if str(STM_DIR) not in sys.path:
    sys.path.insert(0, str(STM_DIR))

import config  # noqa: E402  (STM's config)

USER_ID = 4242
CHAT_ID = 4242


class FakeTG:
    def __init__(self):
        self.messages: dict[int, dict] = {}
        self.order: list[int] = []
        self.answers: list[str | None] = []
        self.next_id = 100

    def send_message(self, text, thread_id=None, reply_markup=None, parse_mode=None, chat_id=None,
                     reply_to_message_id=None):
        import telegram_api as tg
        self.next_id += 1
        mid = self.next_id
        self.messages[mid] = {"text": text, "kb": reply_markup, "chat_id": chat_id or config.CHAT_ID,
                              "thread_id": thread_id}
        self.order.append(mid)
        tg._remember(chat_id or config.CHAT_ID, thread_id, mid)
        return mid

    def edit_message(self, message_id, text, reply_markup=None, parse_mode=None, chat_id=None):
        if message_id not in self.messages:
            return False
        self.messages[message_id].update(text=text, kb=reply_markup)
        return True

    def answer_callback_query(self, cq_id, text=None):
        self.answers.append(text)

    def send_document(self, content, filename, caption=None, thread_id=None, chat_id=None):
        return self.send_message(f"[document {filename}]", thread_id, chat_id=chat_id)

    def set_my_commands(self, commands):
        return None

    # helpers
    def last(self) -> dict:
        return self.messages[self.order[-1]]

    def last_id(self) -> int:
        return self.order[-1]

    def buttons(self, mid: int | None = None) -> list[tuple[str, str]]:
        m = self.messages[mid or self.last_id()]
        kb = m.get("kb") or {}
        return [(b["text"], b["callback_data"]) for row in kb.get("inline_keyboard", []) for b in row]


class Harness:
    def __init__(self, tg_fake: FakeTG):
        self.tg = tg_fake
        from growth import gdb, session, sched, clock
        self.gdb, self.session, self.sched, self.clock = gdb, session, sched, clock

    @property
    def user(self):
        return self.gdb.get_user(USER_ID)

    def tap(self, label: str, mid: int | None = None) -> None:
        mid = mid or self.tg.last_id()
        for text, data in self.tg.buttons(mid):
            if text.startswith("✓ "):
                text = text[2:]
            if text == label or text.startswith(label):
                cq = {"id": "cq", "message": {"chat": {"id": CHAT_ID}, "message_id": mid},
                      "from": {"id": USER_ID}, "data": data}
                self.session.handle_callback(cq, data.split(":"))
                return
        raise AssertionError(f"no button {label!r} on message {mid}: {self.tg.buttons(mid)}")

    def text(self, text: str) -> bool:
        return self.session.claim_text(USER_ID, CHAT_ID, None, text, {"from": {"id": USER_ID}})

    def reply(self, mid: int, text: str) -> None:
        self.session.handle_text(USER_ID, mid, text, {"from": {"id": USER_ID}})

    def card(self) -> str:
        return self.tg.last()["text"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    config.DB_PATH = tmp_path / "tasks.db"
    import db
    db.init()
    import telegram_api as tg
    fake = FakeTG()
    for name in ("send_message", "edit_message", "answer_callback_query", "send_document", "set_my_commands"):
        monkeypatch.setattr(tg, name, getattr(fake, name))
    tg._last_sent.clear()
    import growth
    from growth import clock, gai, gdb, settings, excuses
    monkeypatch.setattr(gai, "call_tool", lambda *a, **k: None)
    monkeypatch.setattr(settings, "ALLOWED_USER_IDS", {USER_ID})
    clock.set_now(datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc))  # 08:00 Los Angeles
    growth.init()
    gdb.ensure_user(USER_ID, CHAT_ID, None, settings.TZ_NAME)
    gdb.update_user(USER_ID, onboarding_step="done")
    excuses.seed_user(USER_ID)
    h = Harness(fake)
    yield h
    clock.reset()
