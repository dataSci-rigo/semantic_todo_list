"""Google Tasks API wrapper — OAuth credential loading/refresh plus thin CRUD
around the Tasks API. Two-way sync logic lives in sync.py. Requires a
one-time local authorization via oauth_setup.py before use."""
from __future__ import annotations

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config

_service = None


def _load_credentials() -> Credentials:
    if not config.GOOGLE_TOKEN_PATH.exists():
        raise RuntimeError(
            f"No Google token at {config.GOOGLE_TOKEN_PATH} — run oauth_setup.py once to authorize."
        )
    creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_PATH), config.GOOGLE_TASKS_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        config.GOOGLE_TOKEN_PATH.write_text(creds.to_json())
    return creds


def get_service():
    global _service
    if _service is None:
        _service = build("tasks", "v1", credentials=_load_credentials())
    return _service


def ensure_list(name: str) -> str:
    service = get_service()
    result = service.tasklists().list(maxResults=100).execute()
    for tl in result.get("items", []):
        if tl["title"] == name:
            return tl["id"]
    created = service.tasklists().insert(body={"title": name}).execute()
    return created["id"]


def insert_task(list_id: str, title: str, notes: str | None = None, completed: bool = False) -> dict:
    service = get_service()
    body: dict = {"title": title}
    if notes:
        body["notes"] = notes
    if completed:
        body["status"] = "completed"
    return service.tasks().insert(tasklist=list_id, body=body).execute()


def update_task(list_id: str, task_id: str, title: str | None = None, notes: str | None = None,
                 completed: bool | None = None) -> dict:
    service = get_service()
    body: dict = {}
    if title is not None:
        body["title"] = title
    if notes is not None:
        body["notes"] = notes
    if completed is not None:
        body["status"] = "completed" if completed else "needsAction"
    return service.tasks().patch(tasklist=list_id, task=task_id, body=body).execute()


def list_tasks(list_id: str) -> list[dict]:
    service = get_service()
    result = service.tasks().list(
        tasklist=list_id, showCompleted=True, showHidden=True, maxResults=100
    ).execute()
    return result.get("items", [])
