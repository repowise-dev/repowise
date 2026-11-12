"""``repowise hosted`` — your repowise.dev account, from the terminal.

``hosted status`` reads ``GET /me/setup-status``: what is set up for a repo on
repowise.dev (index, agent connection, PR bot, coverage) and what is left,
with who does each step and the link for it. It changes nothing and indexes
nothing, so an agent can run it as often as it likes to decide what to do next.

``hosted reviews on`` and ``hosted coverage-pr`` finish the two PR-bot steps an
agent can do itself once a person has installed the bot: turn reviews on for
the repo, and have the bot open its coverage setup PR. Both use the stored
``repowise login`` (write scope) and never see a token.

``--format json`` prints the server's payload as it came, so the contract an
agent reads is the platform's, not a second one restated here. Signed out is a
state, not a failure: it prints ``{"account": {"signed_in": false}, ...}`` and
exits 0. Unreachable, an older server without the route, or any other refusal
exits 1 with an ``error`` the caller can branch on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import click
from rich.markup import escape

from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option

#: The read the command wraps.
SETUP_STATUS_PATH = "me/setup-status"

_SIGN_IN = "Run 'repowise login' (or 'repowise login --device' without a browser)."

#: Where a person installs the PR bot. Installing is theirs to do, never the CLI's.
BOT_INSTALL_URL = "https://github.com/apps/repowise-bot/installations/new"


@click.group("hosted")
def hosted_group() -> None:
    """Your Repowise hosted account (repowise.dev), from this terminal."""


@hosted_group.command("status")
@click.option(
    "--repo",
    "repo_slug",
    default=None,
    metavar="OWNER/NAME",
    help="The repo to report on. Default: this checkout's GitHub origin, else the "
    "account's most recently active repo.",
)
@format_option(help="table prints for a person; json prints the platform's payload as it came.")
def hosted_status(repo_slug: str | None, fmt: str) -> None:
    """What is set up on repowise.dev for a repo, and what is left to do."""
    payload, error = fetch_setup_status(_repo_param(repo_slug))
    if fmt == "json":
        emit_json(payload)
        if error is not None and error != "signed_out":
            raise SystemExit(1)
        return
    if error is not None:
        console.print(escape(payload["message"]))
        if error != "signed_out":
            raise SystemExit(1)
        return
    _render(payload)


def _repo_param(repo_slug: str | None) -> str | None:
    """``owner/name`` from ``--repo``, else this checkout's GitHub origin, else None."""
    from repowise.cli.platform.publish import parse_repo_slug, read_remote

    if repo_slug is not None:
        parsed = parse_repo_slug(repo_slug)
        if parsed is None:
            raise click.UsageError(f"--repo must be owner/name, got {repo_slug!r}.")
    else:
        parsed = read_remote(Path.cwd())
    return "/".join(parsed) if parsed else None


def fetch_setup_status(repo: str | None) -> tuple[dict[str, Any], str | None]:
    """``(payload, error)``: the server's payload and ``None``, or a local one and its code.

    Error codes: ``signed_out``, ``unsupported`` (the server has no such route
    yet), ``offline``, ``http_<status>``.
    """
    from repowise.cli.platform import auth
    from repowise.cli.platform.client import default_client

    if not auth.auth_headers():
        return _signed_out(), "signed_out"
    params = {"via": "cli", **({"repo": repo} if repo else {})}
    status, body = default_client.get_json(SETUP_STATUS_PATH, params=params, timeout=10.0)
    if status == 200:
        return body, None
    failure = (
        _unsupported("setup status") if status in (404, 405) else _http_error(status, body)
    )
    return failure, failure["error"]


def _unsupported(what: str) -> dict[str, Any]:
    """The server answered, but has no such route: an older repowise.dev."""
    return _error(
        "unsupported", f"repowise.dev does not offer {what} yet. Try again after the next update."
    )


def _http_error(status: int, body: dict[str, Any]) -> dict[str, Any]:
    """The error object every hosted call shares: offline, signed out, or refused."""
    if status == 0:
        return _error("offline", "Could not reach repowise.dev. Check the connection.")
    if status == 401:
        return _signed_out()
    raw = body.get("detail")
    detail = raw.get("message") if isinstance(raw, dict) else raw
    return _error(
        f"http_{status}", f"repowise.dev refused the request ({detail or f'HTTP {status}'})."
    )


# ---------------------------------------------------------------------------
# PR bot actions
# ---------------------------------------------------------------------------


def _repo_option(func: Any) -> Any:
    return click.option(
        "--repo",
        "repo_slug",
        default=None,
        metavar="OWNER/NAME",
        help="The repo. Default: this checkout's GitHub origin.",
    )(func)


@hosted_group.group("reviews")
def reviews_group() -> None:
    """Pull request reviews by the Repowise bot."""


@reviews_group.command("on")
@_repo_option
@format_option(help="table prints for a person; json prints the outcome as one object.")
def reviews_on(repo_slug: str | None, fmt: str) -> None:
    """Turn on PR reviews for a repo the bot is installed on."""
    _bot_action(
        repo_slug,
        fmt,
        method="PATCH",
        path="github-app/bot-repos/{repo_id}/enabled",
        payload={"enabled": True},
        done="PR reviews are on for {repo}.",
    )


@hosted_group.command("coverage-pr")
@_repo_option
@format_option(help="table prints for a person; json prints the outcome as one object.")
def coverage_pr(repo_slug: str | None, fmt: str) -> None:
    """Have the bot open a draft PR that sets up coverage uploads for a repo."""
    _bot_action(
        repo_slug,
        fmt,
        method="POST",
        path="github-app/bot-repos/{repo_id}/coverage-setup/pr",
        payload=None,
        done="The bot opened its coverage setup PR for {repo}. Merging it is yours to do.",
    )


def _bot_action(
    repo_slug: str | None,
    fmt: str,
    *,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    done: str,
) -> None:
    """Resolve the bot's id for the repo, send one request, report the outcome."""
    repo = _repo_param(repo_slug)
    if repo is None:
        raise click.UsageError(
            "This checkout has no GitHub 'origin' remote. Pass --repo owner/name."
        )
    result = run_bot_action(repo, method=method, path=path, payload=payload)
    if result.get("ok"):
        result["message"] = done.format(repo=repo)
    if fmt == "json":
        emit_json(result)
    else:
        console.print(escape(result["message"]))
    if not result.get("ok"):
        raise SystemExit(1)


def run_bot_action(
    repo: str, *, method: str, path: str, payload: dict[str, Any] | None
) -> dict[str, Any]:
    """One PR-bot action on *repo*: ``{"ok": true, "repo", "repo_id", "result"}``
    or ``{"ok": false, "repo", "error", "message"}``.

    The bot's id for the repo comes from the setup status (the same read as
    ``hosted status``): its bot and coverage steps carry it when the bot covers
    the repo, so "not installed" is known before anything is sent.
    """
    from repowise.cli.platform.client import default_client

    setup, error = fetch_setup_status(repo)
    if error is not None:
        return {"ok": False, "repo": repo, **setup}
    repo_id = _bot_repo_id(setup)
    if repo_id is None:
        return {"ok": False, "repo": repo, **_not_installed(repo)}
    status, body = default_client.send_json(
        method, path.format(repo_id=quote(repo_id, safe="")), payload, timeout=60.0
    )
    if 200 <= status < 300:
        return {"ok": True, "repo": repo, "repo_id": repo_id, "result": body}
    return {"ok": False, "repo": repo, "repo_id": repo_id, **_refusal(status, body, repo)}


def _bot_repo_id(setup: dict[str, Any]) -> str | None:
    """The bot's id for the setup status's repo, from the first step carrying one."""
    for step in setup.get("steps") or []:
        if isinstance(step, dict) and step.get("repo_id"):
            return str(step["repo_id"])
    return None


def _not_installed(repo: str) -> dict[str, Any]:
    return _error(
        "not_installed",
        f"The Repowise PR bot does not cover {repo} on your account. A person installs "
        f"it (or adds this repo to it) at {BOT_INSTALL_URL}, then run this again.",
    )


def _refusal(status: int, body: dict[str, Any], repo: str) -> dict[str, Any]:
    """The error object for a refused bot request, worded for the person reading it."""
    raw = body.get("detail")
    detail = raw.get("message") if isinstance(raw, dict) else raw
    if status == 402:
        return _error(
            "needs_plan",
            f"{repo} is private, and PR reviews on private repos need a paid plan. "
            "A person can upgrade at https://repowise.dev/settings?tab=billing",
        )
    if status == 403:
        return _error(
            "forbidden",
            f"repowise.dev refused the change ({detail or 'HTTP 403'}). Most often this "
            "sign-in can read but not change settings: run 'repowise logout', then "
            "'repowise login' to sign in with write access, or use an API key with write "
            "access.",
        )
    if status == 404:
        return _not_installed(repo)
    if status == 409:
        failure = _error(
            "permission_needed",
            str(detail or "The bot needs more GitHub permissions before it can open the PR.")
            + " A person approves them on GitHub, then run this again.",
        )
        if isinstance(raw, dict):
            failure["card"] = raw  # the setup card: which permission, and where
        return failure
    if status in (502, 504):
        return _error(
            "pending",
            "The bot did not finish in time. It may still open the PR; run this again "
            "in a minute.",
        )
    return _http_error(status, body)


def _signed_out() -> dict[str, Any]:
    return {
        "account": {"signed_in": False, "user": None},
        "error": "signed_out",
        "message": f"Not signed in to repowise.dev. {_SIGN_IN}",
    }


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def _render(payload: dict[str, Any]) -> None:
    from rich.table import Table

    _render_header(payload)
    steps = [step for step in payload.get("steps") or [] if isinstance(step, dict)]
    if steps:
        table = Table(title="Setup")
        for column in ("Step", "Done", "Who", "Next"):
            table.add_column(column, style="cyan" if column == "Step" else None)
        for step in steps:
            table.add_row(*_step_cells(step))
        console.print(table)
    if payload.get("complete"):
        console.print("[green]Setup is complete.[/green]")


def _render_header(payload: dict[str, Any]) -> None:
    """Who is signed in, and the repo's hosted index."""
    account = payload.get("account") or {}
    tier = payload.get("effective_tier") or payload.get("tier") or "free"
    console.print(f"Signed in as [bold]{escape(str(account.get('user') or '?'))}[/bold] ({tier})")
    repo = payload.get("repo")
    if isinstance(repo, dict) and repo.get("full_name"):
        commit = f" at {repo['indexed_commit']}" if repo.get("indexed_commit") else ""
        console.print(f"Repo [bold]{escape(repo['full_name'])}[/bold]: {repo.get('status')}{commit}")
    else:
        console.print("[dim]No repo on repowise.dev yet. Run 'repowise publish' in a checkout.[/dim]")


def _step_cells(step: dict[str, Any]) -> tuple[str, str, str, str]:
    """One setup step as table cells; the link shows only while the step is open."""
    action = escape(str(step.get("action") or ""))
    if step.get("url") and not step.get("done"):
        action = f"{action}\n[dim]{escape(str(step['url']))}[/dim]"
    optional = " (optional)" if step.get("optional") else ""
    done = "[green]yes[/green]" if step.get("done") else "no"
    return f"{step.get('id')}{optional}", done, str(step.get("who") or ""), action
