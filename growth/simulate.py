#!/usr/bin/env python3
"""Drive stm_bot + growth locally with fake Telegram updates (never polls).

Uses a scratch database, prints every card with numbered buttons, stubs the
Anthropic calls (unless --live-ai) and moves the growth clock on `sleep`.

Commands (REPL, or one per line in --script):
  /cmd args        send a command as the owner        text ...   send plain text
  reply <id> text  reply to message <id>              tap <n|label>  tap a button on the last card
  sleep <sec>      advance the clock, then tick       tick       run growth.tick()
  show [id]        print a card again                 quit

Example:  python -m growth.simulate --fresh --script growth/tests/day.sim
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
from datetime import datetime, timezone

STM_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(STM_DIR) not in sys.path:
    sys.path.insert(0, str(STM_DIR))


class Console:
    def __init__(self):
        self.messages: dict[int, dict] = {}
        self.order: list[int] = []
        self.next_id = 1000

    def _print(self, mid: int) -> None:
        m = self.messages[mid]
        import re
        text = re.sub(r"</?(b|i|code)>", "", m["text"]).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        print(f"\n┌─ #{mid}")
        for line in text.splitlines():
            print(f"│ {line}")
        buttons = [b for row in (m.get("kb") or {}).get("inline_keyboard", []) for b in row]
        if buttons:
            print("│ " + "  ".join(f"[{i + 1}:{b['text']}]" for i, b in enumerate(buttons)))
        print("└─")

    def send_message(self, text, thread_id=None, reply_markup=None, parse_mode=None, chat_id=None,
                     reply_to_message_id=None):
        import telegram_api as tg
        import config
        self.next_id += 1
        mid = self.next_id
        self.messages[mid] = {"text": text, "kb": reply_markup}
        self.order.append(mid)
        tg._remember(chat_id or config.CHAT_ID, thread_id, mid)
        self._print(mid)
        return mid

    def edit_message(self, message_id, text, reply_markup=None, parse_mode=None, chat_id=None):
        if message_id not in self.messages:
            return False
        self.messages[message_id].update(text=text, kb=reply_markup)
        self._print(message_id)
        return True

    def answer_callback_query(self, cq_id, text=None):
        if text:
            print(f"  (toast) {text}")

    def send_document(self, content, filename, caption=None, thread_id=None, chat_id=None):
        return self.send_message(f"[document {filename}, {len(content)} bytes] {caption or ''}", thread_id,
                                 chat_id=chat_id)

    def set_my_commands(self, commands):
        print(f"  (menu: {len(commands)} commands)")

    def get_updates(self, offset, timeout=30):
        return []

    def buttons(self, mid=None):
        m = self.messages[mid or self.order[-1]]
        return [b for row in (m.get("kb") or {}).get("inline_keyboard", []) for b in row]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=str(STM_DIR / "data" / "simulate.db"), help="scratch sqlite file")
    parser.add_argument("--fresh", action="store_true", help="delete the scratch db first")
    parser.add_argument("--script", help="file with one command per line")
    parser.add_argument("--live-ai", action="store_true", help="really call Anthropic (costs money)")
    parser.add_argument("--now", help="pin the clock, ISO local time e.g. 2026-09-18T08:00")
    args = parser.parse_args()

    import config
    config.DB_PATH = pathlib.Path(args.db)
    if args.fresh and config.DB_PATH.exists():
        for suffix in ("", "-wal", "-shm"):
            p = pathlib.Path(str(config.DB_PATH) + suffix)
            if p.exists():
                p.unlink()
    import telegram_api as tg
    console = Console()
    for name in ("send_message", "edit_message", "answer_callback_query", "send_document", "set_my_commands",
                 "get_updates"):
        setattr(tg, name, getattr(console, name))
    import ai
    ai.route_message = lambda text, open_tasks: {"intent": "capture"}
    ai.parse_text_to_tasks = lambda text: [{"title": text[:60], "description": None}]
    import db
    db.init()
    import growth
    from growth import clock, gai, settings
    if not args.live_ai:
        gai.call_tool = lambda *a, **k: None
    if args.now:
        clock.set_now(datetime.fromisoformat(args.now).replace(tzinfo=settings.TZ))
    growth.init()
    import stm_bot
    growth.hooks.sync_now = lambda max_age=120: False
    uid = settings.OWNER_USER_ID
    chat = config.CHAT_ID
    seq = {"n": 1}

    def message(text: str, reply_to: int | None = None) -> None:
        seq["n"] += 1
        msg = {"message_id": seq["n"], "from": {"id": uid}, "chat": {"id": chat, "type": "private"}, "text": text}
        if reply_to:
            msg["reply_to_message"] = {"message_id": reply_to}
        stm_bot.process_update({"update_id": seq["n"], "message": msg})

    def tap(which: str) -> None:
        if not console.order:
            print("  no card to tap")
            return
        mid = console.order[-1]
        buttons = console.buttons(mid)
        target = None
        if which.isdigit() and 1 <= int(which) <= len(buttons):
            target = buttons[int(which) - 1]
        else:
            for b in buttons:
                if b["text"].lstrip("✓ ").lower().startswith(which.lower()):
                    target = b
                    break
        if target is None:
            print(f"  no such button: {which}; have {[b['text'] for b in buttons]}")
            return
        seq["n"] += 1
        cq = {"id": str(seq["n"]), "from": {"id": uid}, "message": {"chat": {"id": chat}, "message_id": mid},
              "data": target["callback_data"]}
        stm_bot.process_update({"update_id": seq["n"], "callback_query": cq})

    def run(line: str) -> bool:
        line = line.strip()
        if not line or line.startswith("#"):
            return True
        if line in ("quit", "exit"):
            return False
        if line.startswith("/"):
            message(line)
        elif line.startswith("text "):
            message(line[5:])
        elif line.startswith("reply "):
            _, mid, *rest = line.split(" ", 2)
            message(rest[0] if rest else "", reply_to=int(mid))
        elif line.startswith("tap "):
            tap(line[4:].strip())
        elif line.startswith("sleep "):
            clock.advance(float(line[6:]))
            growth.tick()
            print(f"  (clock {clock.local_now(settings.TZ).strftime('%H:%M:%S')})")
        elif line == "tick":
            growth.tick()
        elif line.startswith("show"):
            parts = line.split()
            console._print(int(parts[1]) if len(parts) > 1 else console.order[-1])
        else:
            print("  ? commands: /cmd, text, reply, tap, sleep, tick, show, quit")
        return True

    print(f"simulate: db={config.DB_PATH} user={uid} clock={clock.local_now(settings.TZ):%Y-%m-%d %H:%M}")
    if args.script:
        for line in open(args.script, encoding="utf-8"):
            print(f">>> {line.rstrip()}")
            if not run(line):
                break
        return
    while True:
        try:
            line = input(">>> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not run(line):
            break


if __name__ == "__main__":
    main()
