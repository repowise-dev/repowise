"""Hosted-account auth: PKCE sign-in, token refresh, and header assembly.

The browser flow (``repowise login``) is OAuth 2.1 authorization-code with
PKCE against the hosted platform: the CLI binds an ephemeral loopback port,
opens the consent page in the browser, exchanges the returned code, and
persists the token pair via :mod:`repowise.cli.platform.credentials`.

The device flow (``repowise login --device``) is RFC 8628, for a machine with
no browser: the CLI asks for a code, a person approves it on any device, and
the CLI polls the same token endpoint. Its tokens are stored the same way.

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
from collections.abc import Callable
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
    src: str = "cli_login",
) -> str:
    from urllib.parse import urlencode

    from repowise.cli.platform.links import attribution_params

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
    # Where the visit came from, so a signup that starts here is credited to
    # the CLI command that sent it. The install id rides along only with
    # telemetry on; the site uses it to tie this install's usage to the new
    # account.
    params.update(attribution_params(src))
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


#: RFC 8628 grant type for the device authorization flow.
DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

#: RFC 8628 section 3.5: a ``slow_down`` adds this many seconds to the interval.
SLOW_DOWN_SECONDS = 5


def request_device_code(device_name: str | None) -> tuple[int, dict[str, Any]]:
    """``POST /oauth/device/code`` for the CLI client: ``(status, body)``.

    The device name is sent plain ASCII (the server shows it on the approval
    page and in connected apps). No answer at all is ``(0, {})``.
    """
    from repowise.cli.platform.client import default_client

    form = {"client_id": CLIENT_ID, "scope": SCOPES}
    ascii_name = (device_name or "").encode("ascii", "ignore").decode("ascii").strip()
    if ascii_name:
        form["device_name"] = ascii_name[:80]
    return default_client.post_form("oauth/device/code", form, timeout=15.0)


def poll_device_token(
    device_code: str,
    *,
    interval: int,
    expires_in: int,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Poll the token endpoint until the device grant settles (RFC 8628 3.4, 3.5).

    Returns ``("ok", token_response)``, or ``(error, body)`` for a terminal
    answer: ``access_denied``, ``expired_token``, ``invalid_grant``,
    ``unauthorized_client``, ``unsupported_grant_type`` or any other error the
    server names. ``authorization_pending`` keeps polling at *interval*;
    ``slow_down`` adds :data:`SLOW_DOWN_SECONDS` to it for the rest of the
    flow, as does an HTTP 429. A request that gets no answer, a 5xx, or a reply
    that names no OAuth error is retried until the code expires, which is
    reported as ``expired_token``.
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    deadline = clock() + max(expires_in, 1)
    while clock() < deadline:
        sleep(interval)
        status, body = _token_request(
            {"grant_type": DEVICE_CODE_GRANT, "device_code": device_code, "client_id": CLIENT_ID}
        )
        if status == 200 and body.get("access_token"):
            return "ok", body
        error = body.get("error")
        if error == "slow_down" or status == 429:
            interval += SLOW_DOWN_SECONDS
        elif error == "authorization_pending" or status == 0 or status >= 500 or not error:
            # No answer, a server or proxy blip, or a reply that is not an
            # OAuth error: the code may still be approved, so keep polling
            # until it expires rather than throwing it away.
            continue
        else:
            return str(error), body
    return "expired_token", {}


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
