import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

_CREDENTIALS_PATH = Path(os.environ.get("GMAIL_CREDENTIALS_PATH", "credentials.json"))
_TOKEN_PATH = Path(os.environ.get("GMAIL_TOKEN_PATH", "token.json"))

_service = None

def get_gmail_service():
    """
    Returns a cached Gmail API service client. Runs the browser OAuth
    consent flow on first use only; every call after that (including
    across process restarts, via token.json) just refreshes silently.
 
    Synchronous, deliberately — google-api-python-client is a sync
    library. Callers in async code (email_client.py, reply_poller.py)
    wrap actual API calls in asyncio.to_thread, same treatment imaplib's
    blocking calls got before.
    """
    global _service
    if _service is not None:
        return _service

    creds = None
    if _TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _CREDENTIALS_PATH.exists():
                raise RuntimeError(
                    f"{_CREDENTIALS_PATH} not found. Download OAuth client "
                    f"credentials from Google Cloud Console (see this module's "
                    f"docstring) and place them there, or set "
                    f"GMAIL_CREDENTIALS_PATH to point at the file."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_PATH.write_text(creds.to_json())

    _service = build("gmail", "v1", credentials=creds)
    return _service