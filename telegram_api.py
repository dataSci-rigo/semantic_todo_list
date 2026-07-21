"""Thin synchronous Telegram Bot API wrapper (mirrors the requests-based
pattern used elsewhere in this repo — see todo_list/pinger.py — since PTB
async has repeatedly dropped messages here)."""
from __future__ import annotations

import requests

import config

_BASE_URL = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
_FILE_URL = f"https://api.telegram.org/file/bot{config.TELEGRAM_TOKEN}"


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
                  reply_markup: dict | None = None) -> int | None:
    payload: dict = {"chat_id": config.CHAT_ID, "text": text}
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{_BASE_URL}/sendMessage", json=payload, timeout=10)
        data = resp.json()
        if data.get("ok"):
            return data["result"]["message_id"]
        print(f"  sendMessage failed: {data}")
    except Exception as e:
        print(f"  send error: {e}")
    return None


def edit_message(message_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload: dict = {"chat_id": config.CHAT_ID, "message_id": message_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{_BASE_URL}/editMessageText", json=payload, timeout=10)
        data = resp.json()
        if not data.get("ok") and "message is not modified" not in data.get("description", ""):
            print(f"  editMessageText failed: {data}")
    except Exception as e:
        print(f"  edit error: {e}")


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
