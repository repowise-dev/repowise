"""Hosted-account auth: PKCE sign-in, token refresh, and header assembly.

The browser flow (``repowise login``) is OAuth 2.1 authorization-code with
PKCE against the hosted platform: the CLI binds an ephemeral loopback port,
opens the consent page in the browser, exchanges the returned code, and
persists the token pair via :mod:`repowise.cli.platform.credentials`.

:func:`auth_headers` is the fail-soft read side consumed by
``PlatformClient._auth_headers``: it transparently refreshes an expired
access token (single-flight across processes) and returns ``{}`` whenever
the user is signed out, offline, or the grant was revoked — a platform call
then simply proceeds anonymously.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from typing import Any

from repowise.cli.platform import credentials

#: First-party public client (PKCE only, no secret) registered on the hosted
#: authorization server for the CLI.
CLIENT_ID = "repowise-cli"

#: Front-channel consent page (hosted frontend). Production only, matching
#: the PlatformClient design rule.
AUTHORIZE_URL = "https://repowise.dev/oauth/authorize"

#: Scopes the CLI asks for: read everything, plus the write actions the
#: product exposes to signed-in tooling (reindex, docs generation).
SCOPES = "read write"


def make_pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for S256 PKCE."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(
    *,
    redirect_uri: str,
    code_challenge: str,
    state: str,
    device_name: str | None,
) -> str:
    from urllib.parse import urlencode

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if device_name:
        params["device_name"] = device_name
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _token_request(form: dict[str, str]) -> tuple[int, dict[str, Any]]:
    from repowise.cli.platform.client import default_client

    return default_client.post_form("oauth/token", form, timeout=15.0)


def exchange_code(
    *, code: str, redirect_uri: str, code_verifier: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Exchange an authorization code for tokens.

    Returns ``(token_response, None)`` on success or ``(None, error_message)``
    on failure — login is interactive, so errors surface instead of being
    swallowed.
    """
    status, body = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
            "client_id": CLIENT_ID,
        }
    )
    if status == 200 and body.get("access_token"):
        return body, None
    detail = body.get("error_description") or body.get("error") or f"HTTP {status or 'error'}"
    return None, f"Token exchange failed: {detail}"


def credentials_from_token_response(
    body: dict[str, Any], *, device_name: str | None
) -> dict[str, Any]:
    return {
        "token_kind": "oauth",
        "client_id": CLIENT_ID,
        "access_token": body["access_token"],
        "access_expires_at": int(time.time()) + int(body.get("expires_in") or 3600),
        "refresh_token": body.get("refresh_token"),
        "scope": body.get("scope"),
        "device_name": device_name,
    }


def _refresh(creds: dict[str, Any]) -> dict[str, Any] | None:
    """Rotate the refresh token; persist and return the new credentials.

    On ``invalid_grant`` (revoked/expired server-side) the stored credentials
    are marked stale so subsequent calls stay anonymous instead of retrying
    the dead token on every command. On network failure nothing is written —
    the next call retries.
    """
    refresh_token = creds.get("refresh_token")
    if not refresh_token:
        credentials.mark_stale()
        return None
    status, body = _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": creds.get("client_id") or CLIENT_ID,
        }
    )
    if status == 200 and body.get("access_token"):
        updated = dict(creds)
        updated.update(credentials_from_token_response(body, device_name=creds.get("device_name")))
        updated.pop("stale", None)
        # Preserve the account snapshot for whoami/doctor.
        credentials.save(updated)
        return updated
    if status == 400:
        credentials.mark_stale()
    return None


def get_valid_credentials() -> dict[str, Any] | None:
    """Return usable credentials, refreshing if needed. ``None`` = signed out.

    Refresh is single-flight across processes: rotation revokes the presented
    token, so a concurrent loser re-reads what the winner persisted.
    """
    creds = credentials.load()
    if creds is None or creds.get("stale"):
        return None
    if not credentials.is_access_expired(creds):
        return creds
    with credentials.refresh_lock() as acquired:
        # Re-read either way: while we waited (or held the lock), another
        # process may have completed the rotation.
        latest = credentials.load()
        if latest is None or latest.get("stale"):
            return None
        if not credentials.is_access_expired(latest):
            return latest
        if not acquired:
            return None
        return _refresh(latest)


def auth_headers() -> dict[str, str]:
    """Bearer header for platform calls, or ``{}`` when signed out.

    Never raises: any failure (corrupt store, offline refresh, revoked grant)
    degrades to anonymous, matching the PlatformClient fail-silent contract.
    """
    try:
        creds = get_valid_credentials()
    except Exception:
        return {}
    if creds is None:
        return {}
    return {"Authorization": f"Bearer {creds['access_token']}"}


def fetch_account() -> dict[str, Any] | None:
    """GET /auth/me with the stored credentials. ``None`` when signed out or
    unreachable."""
    if not auth_headers():
        return None
    from repowise.cli.platform.client import default_client

    return default_client.get("auth/me", timeout=5.0)


def store_account_snapshot(account: dict[str, Any]) -> None:
    """Cache the identity fields whoami/doctor show when offline."""
    creds = credentials.load()
    if creds is None:
        return
    creds["account"] = {
        "id": account.get("id"),
        "github_username": account.get("github_username"),
        "email": account.get("email"),
        "tier": account.get("tier"),
    }
    credentials.save(creds)
