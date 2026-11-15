"""``repowise login`` / ``logout`` / ``whoami`` — hosted-account sign-in.

Sign-in is browser-based OAuth with PKCE: the command binds an ephemeral
loopback port, opens the hosted consent page, and exchanges the returned
code for tokens stored at ``~/.repowise/credentials.json``. On a machine
with no browser (SSH, a container, a cloud agent), ``repowise login --device``
prints a short code to approve from any other device instead, and
``repowise login --with-token`` accepts a personal API key minted in the
hosted dashboard.

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
from rich.markup import escape

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
    """Best-effort: stitch this machine's pre-login telemetry id to the account.

    The HTTP status (``0`` when there was no answer) goes on this run's
    telemetry as ``link_anon_status``, and to stderr under
    ``REPOWISE_TELEMETRY_DEBUG``, so a link that silently fails is visible.
    """
    with contextlib.suppress(Exception):
        from repowise.cli.platform import identity, settings, telemetry
        from repowise.cli.platform.client import default_client

        status = default_client.post_status(
            "auth/link-anon", {"anon_id": identity.get_anonymous_id()}
        )
        telemetry.add_command_outcome(link_anon_status=status)
        if settings.debug_mode():
            click.echo(f"[repowise telemetry] auth/link-anon -> HTTP {status}", err=True)


@click.command(name="login")
@click.option(
    "--with-token",
    is_flag=True,
    help="Paste a personal API key (rw_live_...) instead of using the browser. "
    "Reads from stdin when piped, otherwise prompts. For SSH/headless machines.",
)
@click.option(
    "--device",
    "use_device_code",
    is_flag=True,
    help="Sign in without a browser on this machine: prints a code to approve "
    "at repowise.dev/device from any other device.",
)
@click.option(
    "--device-name",
    default=None,
    help="Label this machine in your connected apps (default: hostname).",
)
def login_command(with_token: bool, use_device_code: bool, device_name: str | None) -> None:
    """Sign in to your Repowise account."""
    from repowise.cli.platform import auth, credentials

    if with_token and use_device_code:
        raise click.UsageError("Use one of --with-token or --device, not both.")

    existing = credentials.load()
    if existing and not existing.get("stale"):
        from repowise.cli.platform import telemetry

        # Not a new sign-in, so this run's "ok" must not count as one.
        telemetry.add_command_outcome(already_signed_in=True)
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

    if use_device_code:
        device_sign_in(device)
        return
    browser_sign_in(device, src="cli_login")


def browser_sign_in(device: str | None, *, src: str) -> dict:
    """Run the browser PKCE sign-in and return the account (``{}`` when the
    platform did not answer ``/auth/me``). ``src`` credits a signup that
    starts here to the command that sent the person. Raises
    ``click.ClickException`` when the sign-in does not complete.
    """
    from repowise.cli.platform import auth

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
            src=src,
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
    return _complete_sign_in(body, device)


def _complete_sign_in(token_response: dict, device: str | None) -> dict:
    """Store the tokens, cache the account and greet. Shared by every OAuth sign-in.

    The token itself is never printed.
    """
    from repowise.cli.platform import auth, credentials

    credentials.save(auth.credentials_from_token_response(token_response, device_name=device))
    account = auth.fetch_account()
    if account:
        auth.store_account_snapshot(account)
    _link_anonymous_id()
    _greet(account or {})
    return account or {}


_DEVICE_UNSUPPORTED = (
    "repowise.dev does not offer device sign-in yet. Run 'repowise login' on a "
    "machine with a browser, or 'repowise login --with-token' with an API key."
)

_DEVICE_UNAUTHORIZED = (
    "repowise.dev did not allow this CLI to use device sign-in. Update repowise "
    "('pip install -U repowise'), or run 'repowise login' on a machine with a browser."
)

#: What to say when polling ends without a token, by RFC 8628 error code.
_DEVICE_ERRORS = {
    "access_denied": "Sign-in was denied on the approval page. Nothing was stored.",
    "expired_token": (
        "The code expired before it was approved. Run 'repowise login --device' again."
    ),
    "unauthorized_client": _DEVICE_UNAUTHORIZED,
    "unsupported_grant_type": _DEVICE_UNSUPPORTED,
}


def _bounded_seconds(value: object, *, default: int, low: int, high: int) -> int:
    """*value* as whole seconds within ``[low, high]``; *default* when unreadable."""
    try:
        seconds = int(value) if value is not None else default
    except (TypeError, ValueError):
        seconds = default
    return min(max(seconds, low), high)


def device_sign_in(device: str | None) -> dict:
    """Run the RFC 8628 device sign-in and return the account (``{}`` when
    ``/auth/me`` did not answer). Raises ``click.ClickException`` when the
    sign-in does not complete.

    An ``invalid_grant`` while polling (the code is unknown, used, or belongs
    to another client) starts the flow over once with a fresh code, then stops.
    """
    from repowise.cli.platform import auth

    for attempt in range(2):
        body = _start_device_flow(device)
        # Bounded: a server value we cannot read, or one far outside RFC 8628's
        # norms, must not leave the command waiting for hours.
        expires_in = _bounded_seconds(body.get("expires_in"), default=600, low=60, high=1800)
        interval = _bounded_seconds(body.get("interval"), default=5, low=1, high=60)
        _print_device_prompt(body, expires_in)

        outcome, token = auth.poll_device_token(
            body["device_code"], interval=interval, expires_in=expires_in
        )
        if outcome == "ok":
            return _complete_sign_in(token, device)
        if outcome == "invalid_grant" and attempt == 0:
            console.print(
                "[yellow]That code is no longer valid. Starting over with a new one.[/yellow]"
            )
            continue
        detail = token.get("error_description") or outcome
        raise click.ClickException(
            _DEVICE_ERRORS.get(outcome) or f"Device sign-in failed: {detail}"
        )
    raise click.ClickException(
        "Device sign-in failed twice with an invalid code. Run 'repowise login --device' again."
    )


def _start_device_flow(device: str | None) -> dict:
    """Ask for a device code; the server's answer, or a ``ClickException`` saying why not."""
    from repowise.cli.platform import auth

    status, body = auth.request_device_code(device)
    error = body.get("error")
    if status in (404, 405) or error == "unsupported_grant_type":
        raise click.ClickException(_DEVICE_UNSUPPORTED)
    if error == "unauthorized_client":
        raise click.ClickException(_DEVICE_UNAUTHORIZED)
    if status == 0:
        raise click.ClickException(
            "Could not reach repowise.dev. Check the connection and try again."
        )
    if status != 200 or not body.get("device_code") or not body.get("user_code"):
        detail = body.get("error_description") or error or f"HTTP {status}"
        raise click.ClickException(f"Device sign-in could not start: {detail}")
    return body


def _print_device_prompt(body: dict, expires_in: int) -> None:
    """Where to go and what to type. Server strings are escaped, never markup."""
    verify = escape(str(body.get("verification_uri") or "https://repowise.dev/device"))
    console.print(f"To sign in, open [cyan]{verify}[/cyan] on any device")
    console.print(f"and enter the code [bold]{escape(str(body['user_code']))}[/bold]")
    if body.get("verification_uri_complete"):
        complete = escape(str(body["verification_uri_complete"]))
        console.print(f"(or open [cyan]{complete}[/cyan] with the code filled in).")
    console.print("Approve it only if you started this sign-in yourself, on a machine you control.")
    console.print(f"[dim]Waiting for approval (the code expires in {expires_in // 60} min)...[/dim]")


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
