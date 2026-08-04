#!/usr/bin/env python3
"""One-time local authorization for Google Tasks sync.

Run this yourself in a terminal with a browser available (not headless —
this can't run on the VM). It opens a browser window, you approve access,
and it saves a refresh token to data/google_token.json, which the bot then
uses to authenticate headlessly from then on.

Run: python oauth_setup.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow

import config


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(
        config.GOOGLE_CLIENT_SECRET_PATH, config.GOOGLE_TASKS_SCOPES
    )
    creds = flow.run_local_server(port=0)
    config.GOOGLE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.GOOGLE_TOKEN_PATH.write_text(creds.to_json())
    print(f"Saved token to {config.GOOGLE_TOKEN_PATH}")


if __name__ == "__main__":
    main()
