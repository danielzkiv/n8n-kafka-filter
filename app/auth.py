from __future__ import annotations

import base64
import json
import logging
import os
import secrets

from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from fastapi.responses import RedirectResponse, HTMLResponse
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner

logger = logging.getLogger(__name__)

SESSION_COOKIE = "kf_session"
SESSION_MAX_AGE = 8 * 3600  # 8 hours

_signer: TimestampSigner | None = None
_oauth: OAuth | None = None


def _get_signer() -> TimestampSigner:
    global _signer
    if _signer is None:
        secret = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
        _signer = TimestampSigner(secret)
    return _signer


def get_oauth() -> OAuth:
    global _oauth
    if _oauth is None:
        _oauth = OAuth()
        _oauth.register(
            name="google",
            client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
    return _oauth


def _allowed_emails() -> set[str]:
    raw = os.environ.get("ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def get_session(request: Request) -> dict | None:
    """Return session dict if the cookie is valid, else None."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if not cookie:
        return None
    try:
        data = _get_signer().unsign(cookie, max_age=SESSION_MAX_AGE).decode()
        return json.loads(base64.b64decode(data).decode())
    except (SignatureExpired, BadSignature):
        return None


def make_session_cookie(email: str, name: str) -> str:
    data = base64.b64encode(json.dumps({"email": email, "name": name}).encode()).decode()
    return _get_signer().sign(data).decode()


def is_allowed(email: str) -> bool:
    allowed = _allowed_emails()
    if not allowed:
        return True  # no list = any authenticated Google user is allowed
    return email.lower() in allowed


def auth_enabled() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID"))


def login_page(redirect_to: str = "/ui") -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sign in — n8n Kafka Filter</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center">
  <div class="bg-white rounded-2xl border shadow-sm p-10 w-full max-w-sm text-center">
    <h1 class="text-xl font-bold mb-1">n8n Kafka Filter</h1>
    <p class="text-sm text-gray-400 mb-8">Sign in to access the filter configuration</p>
    <a href="/auth/login?next={redirect_to}"
      class="flex items-center justify-center gap-3 w-full border rounded-lg px-4 py-2.5 text-sm font-medium hover:bg-gray-50 transition-colors">
      <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.08 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.31-8.16 2.31-6.26 0-11.57-3.59-13.46-8.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg>
      Sign in with Google
    </a>
  </div>
</body>
</html>""")


def denied_page(email: str) -> HTMLResponse:
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Access Denied</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center">
  <div class="bg-white rounded-2xl border shadow-sm p-10 w-full max-w-sm text-center">
    <div class="text-3xl mb-4">🚫</div>
    <h1 class="text-lg font-bold mb-2">Access Denied</h1>
    <p class="text-sm text-gray-400 mb-6">{email} is not on the allowed list.</p>
    <a href="/auth/logout" class="text-sm text-blue-600 hover:underline">Sign out</a>
  </div>
</body>
</html>""", status_code=403)
