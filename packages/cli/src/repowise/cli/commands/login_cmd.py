"""``repowise login`` / ``logout`` / ``whoami`` — hosted-account sign-in.

Sign-in is browser-based OAuth with PKCE: the command binds an ephemeral
loopback port, opens the hosted consent page, and exchanges the returned
code for tokens stored at ``~/.repowise/credentials.json``. For headless
machines, ``repowise login --with-token`` accepts a personal API key minted
in the hosted dashboard instead.

Signing in is always optional: every local feature works without it. A
login adds the hosted layer (your indexed repos on repowise.dev, reindex
from local tools, account status in doctor).
"""

from __future__ import annotations

import contextlib
import platform as _platform
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import click

from repowise.cli.helpers import console

#: How long the command waits for the user to finish the browser consent.
_CALLBACK_TIMEOUT_SECONDS = 300

_SUCCESS_HTML = """<!doctype html>
<html><head><title>Repowise</title></head>
<body style="font-family: system-ui, sans-serif; display: flex; align-items: center;
             justify-content: center; height: 90vh; color: #333;">
  <div style="text-align: center;">
    <h2>You're signed in</h2>
    <p>Return to your terminal to continue.</p>
  </div>
</body></html>"""

_DENIED_HTML = _SUCCESS_HTML.replace("You're signed in", "Sign-in was cancelled").replace(
    "Return to your terminal to continue.", "You can close this tab."
)


class _CallbackResult:
    def __init__(self) -> None:
        self.code: str | None = None
        self.state: str | None = None
        self.error: str | None = None
        self.received = threading.Event()


def _make_handler(result: _CallbackResult) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # http.server callback API name
            parsed = urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = parse_qs(parsed.query)
            result.code = (params.get("code") or [None])[0]
            result.state = (params.get("state") or [None])[0]
            result.error = (params.get("error") or [None])[0]
            body = _DENIED_HTML if result.error else _SUCCESS_HTML
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            result.received.set()

        def log_message(self, *args: object) -> None:  # silence stdlib logging
            pass

    return Handler


def _wait_for_callback(server: HTTPServer, result: _CallbackResult) -> None:
    """Serve requests until the /callback hit lands or the deadline passes."""
    server.timeout = 1.0
    import time

    deadline = time.time() + _CALLBACK_TIMEOUT_SECONDS
    while not result.received.is_set() and time.time() < deadline:
        server.handle_request()


def _default_device_name() -> str | None:
    with contextlib.suppress(Exception):
        return _platform.node()[:80] or None
    return None


def _greet(account: dict) -> None:
    name = account.get("github_username") or account.get("email") or "there"
    tier = account.get("tier") or "free"
    console.print(f"[green]✓[/green] Signed in as [bold]{name}[/bold] ({tier} plan)")
    credits_cents = account.get("llm_credit_balance_cents")
    if isinstance(credits_cents, int) and credits_cents > 0:
        console.print(f"  Credits: ${credits_cents / 100:.2f}")


def _link_anonymous_id() -> None:
    """Best-effort: stitch this machine's pre-login telemetry id to the account."""
    with contextlib.suppress(Exception):
        from repowise.cli.platform import identity
        from repowise.cli.platform.client import default_client

        default_client.post("auth/link-anon", {"anon_id": identity.get_anonymous_id()})


@click.command(name="login")
@click.option(
    "--with-token",
    is_flag=True,
    help="Paste a personal API key (rw_live_...) instead of using the browser. "
    "Reads from stdin when piped, otherwise prompts. For SSH/headless machines.",
)
@click.option(
    "--device-name",
    default=None,
    help="Label this machine in your connected apps (default: hostname).",
)
def login_command(with_token: bool, device_name: str | None) -> None:
    """Sign in to your Repowise account."""
    from repowise.cli.platform import auth, credentials

    existing = credentials.load()
    if existing and not existing.get("stale"):
        account = existing.get("account") or {}
        who = account.get("github_username") or "an account"
        console.print(
            f"Already signed in as [bold]{who}[/bold]. "
            "Run [cyan]repowise logout[/cyan] first to switch accounts."
        )
        return

    device = (device_name or _default_device_name() or "").strip()[:80] or None

    if with_token:
        token = (
            sys.stdin.readline().strip()
            if not sys.stdin.isatty()
            else click.prompt("Personal API key", hide_input=True).strip()
        )
        if not token.startswith("rw_live_"):
            raise click.ClickException(
                "That doesn't look like a Repowise API key (expected rw_live_...). "
                "Create one at repowise.dev under Settings > Editor (MCP)."
            )
        credentials.save({"token_kind": "api_key", "access_token": token, "device_name": device})
        account = auth.fetch_account()
        if account is None:
            credentials.delete()
            raise click.ClickException(
                "The key was rejected by the platform. Check it and try again."
            )
        auth.store_account_snapshot(account)
        _link_anonymous_id()
        _greet(account)
        return

    # Browser PKCE flow.
    verifier, challenge = auth.make_pkce_pair()
    state = secrets.token_urlsafe(16)
    result = _CallbackResult()
    try:
        server = HTTPServer(("127.0.0.1", 0), _make_handler(result))
    except OSError as exc:
        raise click.ClickException(
            f"Could not open a local port for the sign-in callback ({exc}). "
            "Use repowise login --with-token instead."
        ) from exc

    try:
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
        url = auth.build_authorize_url(
            redirect_uri=redirect_uri,
            code_challenge=challenge,
            state=state,
            device_name=device,
        )
        console.print("Opening your browser to sign in to Repowise...")
        console.print(f"If it doesn't open, visit:\n  [cyan]{url}[/cyan]\n")
        webbrowser.open(url)
        _wait_for_callback(server, result)
    finally:
        server.server_close()

    if not result.received.is_set():
        raise click.ClickException(
            "Timed out waiting for the browser sign-in. "
            "Try again, or use repowise login --with-token on this machine."
        )
    if result.error:
        raise click.ClickException(
            "Sign-in was cancelled in the browser."
            if result.error == "access_denied"
            else f"Sign-in failed: {result.error}"
        )
    if result.state != state or not result.code:
        raise click.ClickException("Sign-in response didn't match this login attempt. Try again.")

    body, error = auth.exchange_code(
        code=result.code, redirect_uri=redirect_uri, code_verifier=verifier
    )
    if body is None:
        raise click.ClickException(error or "Token exchange failed.")

    credentials.save(auth.credentials_from_token_response(body, device_name=device))
    account = auth.fetch_account()
    if account:
        auth.store_account_snapshot(account)
    _link_anonymous_id()
    _greet(account or {})


@click.command(name="logout")
def logout_command() -> None:
    """Sign out of your Repowise account on this machine."""
    from repowise.cli.platform import auth, credentials

    creds = credentials.load()
    if creds is None:
        console.print("Not signed in.")
        return

    # Best-effort server-side revocation of this device's grant; the local
    # delete is the part that must succeed.
    if creds.get("token_kind") == "oauth":
        with contextlib.suppress(Exception):
            from repowise.cli.platform.client import default_client

            params = {}
            if creds.get("device_name"):
                params["device_name"] = creds["device_name"]
            default_client.delete(
                f"oauth/connections/{creds.get('client_id') or auth.CLIENT_ID}",
                params=params or None,
            )
    credentials.delete()
    console.print("[green]✓[/green] Signed out. Local features are unaffected.")


@click.command(name="whoami")
def whoami_command() -> None:
    """Show the Repowise account this machine is signed in to."""
    from repowise.cli.platform import auth, credentials

    creds = credentials.load()
    if creds is None:
        console.print("Not signed in. Run [cyan]repowise login[/cyan] to connect your account.")
        return

    account = auth.fetch_account()
    if account:
        auth.store_account_snapshot(account)
        _greet(account)
    else:
        cached = creds.get("account") or {}
        who = cached.get("github_username") or cached.get("email") or "unknown account"
        if creds.get("stale"):
            console.print(
                f"Signed in as [bold]{who}[/bold], but the session has expired or was "
                "revoked. Run [cyan]repowise login[/cyan] to sign in again."
            )
        else:
            console.print(
                f"Signed in as [bold]{who}[/bold] (couldn't reach the platform to verify)."
            )
        return

    kind = "API key" if creds.get("token_kind") == "api_key" else "browser sign-in"
    device = creds.get("device_name")
    detail = f"  Auth: {kind}"
    if device:
        detail += f" on {device}"
    console.print(detail)
