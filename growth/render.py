"""Rendering helpers: HTML escaping, inline keyboards, cards, edit-in-place.

Every dynamic string goes through esc(); parse_mode is always HTML."""
from __future__ import annotations

import html

import telegram_api as tg

from . import gdb, settings


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def kb(rows: list[list[tuple[str, str]]]) -> dict:
    """rows of (label, callback_data). Asserts Telegram's 64-byte limit."""
    out = []
    for row in rows:
        if not row:
            continue
        buttons = []
        for label, data in row:
            if len(data.encode("utf-8")) > settings.CALLBACK_MAX_BYTES:
                raise ValueError(f"callback_data over 64 bytes: {data!r}")
            buttons.append({"text": label[:64], "callback_data": data})
        out.append(buttons)
    return {"inline_keyboard": out}


def card(title: str, k: int | None, n: int | None, body: str, footer: str | None = None) -> str:
    head = f"<b>{esc(title)}</b>"
    if k and n and n > 1:
        head += f" · {k}/{n}"
    parts = [head, body]
    if footer:
        parts.append(f"<i>{esc(footer)}</i>")
    return "\n".join(p for p in parts if p)


def send(chat_id: int, thread_id: int | None, text: str, keyboard: dict | None = None,
         reply_to: int | None = None) -> int | None:
    return tg.send_message(text, thread_id, reply_markup=keyboard, parse_mode="HTML",
                           chat_id=chat_id, reply_to_message_id=reply_to)


def show(touch, text: str, keyboard: dict | None) -> int | None:
    """Edit the touch's card in place; send once if it has no message yet or
    the edit fails (old message). Returns the message id or None."""
    message_id = touch["message_id"]
    markup = keyboard if keyboard is not None else {"inline_keyboard": []}
    if message_id:
        if tg.edit_message(message_id, text, reply_markup=markup, parse_mode="HTML", chat_id=touch["chat_id"]):
            return message_id
    new_id = tg.send_message(text, touch["thread_id"], reply_markup=keyboard, parse_mode="HTML",
                             chat_id=touch["chat_id"])
    if new_id:
        gdb.update_touch(touch["id"], message_id=new_id)
    return new_id


def plain(chat_id: int, thread_id: int | None, text: str) -> int | None:
    return tg.send_message(text, thread_id, parse_mode="HTML", chat_id=chat_id)
