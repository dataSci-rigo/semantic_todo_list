#!/usr/bin/env python3
"""Standalone TaskBus sync driver — the sync account is STM_SYNC_ACCOUNT
from .env (config.SYNC_ACCOUNT); this script refuses to sync as anyone else
once the account is verifiable.

Usage:
  python sync_tasks.py            one full sync cycle (cron-friendly)
  python sync_tasks.py --auth     (re)authorize in a browser — sign in as
                                  the STM_SYNC_ACCOUNT account; saves the token
                                  with the email scope so the account check
                                  becomes enforceable
  python sync_tasks.py --status   show account, shared list, link stats

Legacy tokens minted before the email scope can't prove which account they
are; --status/-sync will warn until you run --auth once.
"""
from __future__ import annotations

import argparse
import sys

import config
import db


def _account_check(strict: bool) -> bool:
    import google_tasks as gt
    if not config.SYNC_ACCOUNT:
        print("⚠ STM_SYNC_ACCOUNT is not set in .env — can't check which Google "
              "account the token belongs to.")
        return not strict
    email = gt.get_account_email()
    if email is None:
        print(f"⚠ Can't verify the Google account (token predates the email scope).\n"
              f"  Expected: {config.SYNC_ACCOUNT_LABEL} — run `python sync_tasks.py --auth` to fix.")
        return not strict
    if email.lower() != config.SYNC_ACCOUNT.lower():
        print(f"✗ Token belongs to {email}, but the sync account must be "
              f"{config.SYNC_ACCOUNT}. Run `python sync_tasks.py --auth`.")
        return False
    print(f"✓ Sync account verified: {email}")
    return True


def cmd_auth() -> int:
    from google_auth_oauthlib.flow import InstalledAppFlow
    print(f"A browser window will open — sign in as {config.SYNC_ACCOUNT_LABEL}.")
    flow = InstalledAppFlow.from_client_secrets_file(
        config.GOOGLE_CLIENT_SECRET_PATH, config.GOOGLE_TASKS_SCOPES
    )
    creds = flow.run_local_server(port=0)
    config.GOOGLE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_TOKEN_PATH.write_text(creds.to_json())
    print(f"Token saved to {config.GOOGLE_TOKEN_PATH}")
    import google_tasks as gt
    gt._service = None
    return 0 if _account_check(strict=True) else 1


def cmd_status() -> int:
    import google_tasks as gt
    ok = _account_check(strict=False)
    list_id = gt.ensure_list(config.GOOGLE_TASKS_LIST_NAME)
    remote = gt.list_tasks(list_id)
    links = db.get_all_links()
    print(f"Shared list: \"{config.GOOGLE_TASKS_LIST_NAME}\" ({list_id})")
    print(f"Remote tasks: {len(remote)} | local links: {len(links)} | "
          f"locked by us: {sum(1 for l in links if l['locked_by_us'])}")
    from taskbus import parse_notes
    apps: dict[str, int] = {}
    for t in remote:
        _, block = parse_notes(t.get("notes"))
        apps[(block or {}).get("app", "(no block)")] = apps.get((block or {}).get("app", "(no block)"), 0) + 1
    print("Writers seen:", ", ".join(f"{a}×{n}" for a, n in sorted(apps.items())))
    return 0 if ok else 1


def cmd_sync() -> int:
    if not _account_check(strict=True):
        return 1
    import sync
    sync.run_sync()
    print("Sync cycle complete.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--auth", action="store_true", help="(re)authorize the sync account")
    parser.add_argument("--status", action="store_true", help="show account + bus state")
    args = parser.parse_args()

    db.init()
    if args.auth:
        return cmd_auth()
    if args.status:
        return cmd_status()
    return cmd_sync()


if __name__ == "__main__":
    sys.exit(main())
