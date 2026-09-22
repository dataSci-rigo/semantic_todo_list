"""Thin synchronous Telegram Bot API wrapper (mirrors the requests-based
pattern used elsewhere in this repo — see todo_list/pinger.py — since PTB
async has repeatedly dropped messages here)."""
from __future__ import annotations

import requests

import config

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
_FILE_URL = f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}"

# (chat_id, thread_id) -> message_id of the last message this process sent there.
# growth uses it to decide whether a plain text is the answer to its open question.
_last_sent: dict[tuple[int, int | None], int] = {}


def last_sent_message_id(chat_id: int, thread_id: int | None) -> int | None:
    return _last_sent.get((chat_id, thread_id))


def _remember(chat_id: int, thread_id: int | None, message_id: int) -> None:
    _last_sent[(chat_id, thread_id)] = message_id


def get_updates(offset: int, timeout: int = 30) -> list[dict]:
    try:
        resp = requests.get(
            f"{_BASE_URL}/getUpdates",
            params={"timeout": timeout, "offset": offset, "limit": 100,
                    "allowed_updates": '["message","callback_query"]'},
            timeout=timeout + 10,
        )
        return resp.json().get("result", [])
    except Exception as e:
        print(f"  poll error: {e}")
        return []


def send_message(text: str, thread_id: int | None = None,
                  reply_markup: dict | None = None, parse_mode: str | None = None,
                  chat_id: int | None = None, reply_to_message_id: int | None = None) -> int | None:
    cid = chat_id or config.CHAT_ID
    payload: dict = {"chat_id": cid, "text": text}
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": reply_to_message_id, "allow_sending_without_reply": True}
    try:
        resp = requests.post(f"{_BASE_URL}/sendMessage", json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            _remember(cid, thread_id, data["result"]["message_id"])
            return data["result"]["message_id"]
        print(f"  sendMessage failed: {data}")
    except Exception as e:
        print(f"  send error: {e}")
    return None


def send_photo(file_id: str, caption: str | None = None, thread_id: int | None = None,
                reply_markup: dict | None = None, chat_id: int | None = None) -> int | None:
    """Re-send a photo Telegram already hosts, by its file_id."""
    cid = chat_id or config.CHAT_ID
    payload: dict = {"chat_id": cid, "photo": file_id}
    if caption:
        payload["caption"] = caption
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{_BASE_URL}/sendPhoto", json=payload, timeout=15)
        data = resp.json()
        if data.get("ok"):
            _remember(cid, thread_id, data["result"]["message_id"])
            return data["result"]["message_id"]
        print(f"  sendPhoto failed: {data}")
    except Exception as e:
        print(f"  send_photo error: {e}")
    return None


def edit_message(message_id: int, text: str, reply_markup: dict | None = None,
                  parse_mode: str | None = None, chat_id: int | None = None) -> bool:
    """Edit in place. Returns True when Telegram accepted it (or nothing changed)."""
    payload: dict = {"chat_id": chat_id or config.CHAT_ID, "message_id": message_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(f"{_BASE_URL}/editMessageText", json=payload, timeout=10)
        data = resp.json()
        if data.get("ok") or "message is not modified" in data.get("description", ""):
            return True
        print(f"  editMessageText failed: {data}")
    except Exception as e:
        print(f"  edit error: {e}")
    return False


def send_document(content: bytes | str, filename: str, caption: str | None = None,
                   thread_id: int | None = None, chat_id: int | None = None) -> int | None:
    """Send a generated file (e.g. an export prompt) as a document."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    cid = chat_id or config.CHAT_ID
    data: dict = {"chat_id": cid}
    if caption:
        data["caption"] = caption
    if thread_id:
        data["message_thread_id"] = thread_id
    try:
        resp = requests.post(f"{_BASE_URL}/sendDocument", data=data,
                              files={"document": (filename, content)}, timeout=30)
        payload = resp.json()
        if payload.get("ok"):
            _remember(cid, thread_id, payload["result"]["message_id"])
            return payload["result"]["message_id"]
        print(f"  sendDocument failed: {payload}")
    except Exception as e:
        print(f"  send_document error: {e}")
    return None


def set_my_commands(commands: list[tuple[str, str]]) -> None:
    """Register the '/' autocomplete menu. commands = [(name, description), ...]"""
    payload = {"commands": [{"command": c, "description": d} for c, d in commands]}
    try:
        resp = requests.post(f"{_BASE_URL}/setMyCommands", json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"  setMyCommands failed: {data}")
    except Exception as e:
        print(f"  setMyCommands error: {e}")


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        requests.post(f"{_BASE_URL}/answerCallbackQuery", json=payload, timeout=10)
    except Exception as e:
        print(f"  answerCallbackQuery error: {e}")


def download_file(file_id: str) -> bytes | None:
    try:
        resp = requests.get(f"{_BASE_URL}/getFile", params={"file_id": file_id}, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"  getFile failed: {data}")
            return None
        file_path = data["result"]["file_path"]
        file_resp = requests.get(f"{_FILE_URL}/{file_path}", timeout=30)
        return file_resp.content
    except Exception as e:
        print(f"  download_file error: {e}")
        return None


def candidates_keyboard(capture_id: int, candidates: list[str]) -> dict:
    rows = [[{"text": c[:64], "callback_data": f"sit:{capture_id}:{i}"}]
            for i, c in enumerate(candidates)]
    rows.append([{"text": "Other", "callback_data": f"sit:{capture_id}:other"}])
    return {"inline_keyboard": rows}
