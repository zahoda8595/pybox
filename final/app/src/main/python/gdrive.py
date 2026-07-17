"""
gdrive.py — Google Drive access via OAuth, authorized by you, per-connection.

HOW THIS IS DIFFERENT FROM "DEEP ACCESS":
  This is Google's own sanctioned mechanism: you see Google's real
  consent screen, you approve exactly what scope of access to grant,
  and you can revoke it anytime at https://myaccount.google.com/permissions
  without touching this app at all. PyBox never sees your Google
  password - only a token Google issues after you approve.

DEFAULT SCOPE (deliberately least-privilege):
  - drive.readonly : read any file in your Drive
  - drive.file     : write access ONLY to files this app itself creates
  If you want broader write access, change SCOPES below to
  ["https://www.googleapis.com/auth/drive"] - that's a one-line change,
  but it's your call to widen it, not a default.

ONE-TIME SETUP YOU HAVE TO DO (Google requires this be tied to your own
account - I can't provision it for you):
  1. Go to https://console.cloud.google.com/apis/credentials
  2. Create a project (or use an existing one).
  3. Enable the "Google Drive API" for it (APIs & Services -> Library).
  4. Create Credentials -> OAuth client ID -> Application type: "Web
     application".
  5. Under "Authorized redirect URIs", add exactly:
         http://127.0.0.1:5000/drive/oauth2callback
  6. Download the JSON, rename it client_secrets.json, and place it at:
         /sdcard/PyBox/client_secrets.json
     (This file is a client identifier, not a secret credential in the
     sensitive sense for installed apps, but keep it out of anywhere
     public regardless - e.g. don't commit it to your GitHub repo.)

USAGE (from the app):
  GET  /drive/authorize        - starts the consent flow (open in WebView)
  GET  /drive/oauth2callback   - Google redirects here after you approve
  GET  /drive/status           - whether a valid token is currently stored
  GET  /drive/files            - list files (query param: q= for a Drive
                                  search query, optional)
  GET  /drive/download/<id>    - download a file's content by its Drive ID
"""

import json
import logging
import os

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]
REDIRECT_URI = "http://127.0.0.1:5000/drive/oauth2callback"

_FILES_DIR = None
_CLIENT_SECRETS_PATH = None


def init(files_dir, client_secrets_path):
    global _FILES_DIR, _CLIENT_SECRETS_PATH
    _FILES_DIR = files_dir
    _CLIENT_SECRETS_PATH = client_secrets_path


def _token_path():
    return os.path.join(_FILES_DIR, "drive_token.json")


def has_client_secrets():
    return _CLIENT_SECRETS_PATH and os.path.exists(_CLIENT_SECRETS_PATH)


def get_credentials():
    """Returns valid Credentials, or None if not yet authorized."""
    path = _token_path()
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        import google.auth.transport.requests
        creds.refresh(google.auth.transport.requests.Request())
        _save_credentials(creds)
    return creds if creds and creds.valid else None


def _save_credentials(creds):
    with open(_token_path(), "w") as f:
        f.write(creds.to_json())


def build_authorize_url():
    flow = Flow.from_client_secrets_file(
        _CLIENT_SECRETS_PATH, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    auth_url, _state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )
    return auth_url


def handle_callback(full_callback_url):
    flow = Flow.from_client_secrets_file(
        _CLIENT_SECRETS_PATH, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(authorization_response=full_callback_url)
    _save_credentials(flow.credentials)
    logging.info("Google Drive: authorization completed and token saved.")


def list_files(query=None, page_size=50):
    creds = get_credentials()
    if not creds:
        return {"error": "not authorized - visit /drive/authorize first"}
    service = build("drive", "v3", credentials=creds)
    resp = service.files().list(
        q=query, pageSize=page_size,
        fields="files(id, name, mimeType, modifiedTime, size)",
    ).execute()
    return {"files": resp.get("files", [])}


def download_file(file_id):
    creds = get_credentials()
    if not creds:
        return None, "not authorized - visit /drive/authorize first"
    service = build("drive", "v3", credentials=creds)
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    return buf.getvalue(), None
