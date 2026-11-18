"""``repowise bot`` — the Repowise PR bot on a repo, from the terminal.

``bot status`` reads the bot's part of the repo's setup status on repowise.dev
(the same read as ``hosted status``): install and claim, reviews on or off, AI
review on or off and why, plan and credit, the last review and its check,
coverage, and the one next step with who does it. It changes nothing, so an
agent can run it as often as it likes.

``bot ai on|off`` sets the repo's review mode (AI review, or the free signal
checks only), then reports the state that follows, including anything still
in the way.

Both use the stored ``repowise login`` and print one JSON object under
``--format json`` in every case, error or not. The ``bot``, ``plan``,
``coverage`` and ``steps`` blocks are the platform's as they came, so the
contract an agent reads is the platform's, not a second one restated here.
"""

from __future__ import annotations

from typing import Any

import click
from rich.markup import escape

from repowise.cli.commands.hosted_cmd import (
    _repo_option,
    _repo_param,
    fetch_setup_status,
    run_bot_action,
)
from repowise.cli.exit_codes import EXIT_NEEDS_HUMAN
from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option

_EXIT_HELP = (
    "Exit codes: 0 nothing waits on a person; 3 a person's step is next (sign in, "
    "install, claim, approve or pay: 'next' says who, what and where); 1 error "
    "(offline, refused, or repowise.dev does not offer this yet)."
)

#: Refusals only a person can clear. Any other failure is an error.
_HUMAN_CODES = frozenset(
    {"signed_out", "not_installed", "needs_plan", "forbidden", "permission_needed", "locked"}
)

#: The setup steps that are the bot's.
_BOT_STEPS = ("bot", "coverage")

#: Step codes that leave nothing to do. A done bot step with any other code
#: still names an action (AI review off, or paused for credit or its limit).
_SETTLED = frozenset({"on", "ai_on", "unknown", "coverage_received"})

_OLDER_SERVER = (
    "repowise.dev does not report AI review, credit or the last review yet; install, "
    "claim, reviews and coverage are current."
)


@click.group("bot", epilog=_EXIT_HELP)
def bot_group() -> None:
    """The Repowise PR bot on GitHub: its state on a repo, and AI review on or off.

    \b
    Start with:  repowise bot status --format json
    If "next" is the agent's, run its action; if it is a person's ("who":
    "human"), hand them its action text and url, then run status again.
    """


@bot_group.command("status", epilog=_EXIT_HELP)
@_repo_option
@format_option(help="table prints for a person; json prints one object for an agent.")
def bot_status(repo_slug: str | None, fmt: str) -> None:
    """The PR bot's state on a repo and the one next step, with who does it.

    \b
    JSON keys:
      bot       installed, enabled, unclaimed, code, claim_url, install_url,
                ai {on, code, mode, depth}, credit, last_review {pr_number,
                status, tier, reason, check_url}, coverage_received
      plan, effective_tier, coverage
      steps     the bot and coverage setup steps
      next      {code, who, action, url}, or null when nothing is left
      complete  true once PR reviews are on
    """
    repo = _require_repo(repo_slug)
    payload, error = bot_state(repo)
    if fmt == "json":
        emit_json(payload)
    elif error is not None:
        console.print(escape(payload["message"]))
    else:
        render_status(payload)
    if code := exit_code(payload, error):
        raise SystemExit(code)


@bot_group.command("ai", epilog=_EXIT_HELP)
@click.argument("state", type=click.Choice(["on", "off"]))
@click.option(
    "--depth",
    type=click.Choice(["light", "auto", "deep"]),
    default=None,
    help="With 'on': how deep each AI review reads. Default: leave it as set (auto).",
)
@_repo_option
@format_option(help="table prints for a person; json prints the outcome and the new state.")
def bot_ai(state: str, depth: str | None, repo_slug: str | None, fmt: str) -> None:
    """Turn AI review on or off for a repo; off keeps the free signal checks.

    AI review needs a paid plan and the bot's install on the repo claimed by
    you. When something is in the way the command says what, who clears it
    and where, and exits 3. The JSON carries "status", the same object as
    'repowise bot status' after the change, or null when it cannot be read.
    """
    if depth and state == "off":
        raise click.UsageError("--depth goes with 'repowise bot ai on'.")
    repo = _require_repo(repo_slug)
    config = {"review_mode": "ai" if state == "on" else "signals"}
    if depth:
        config["review_depth"] = depth
    result = run_bot_action(
        repo,
        method="PATCH",
        path="github-app/bot-repos/{repo_id}/config",
        payload={"config": config},
    )
    status: dict[str, Any] | None = None
    if result.get("error") not in ("signed_out", "offline", "unsupported"):
        read, read_error = bot_state(repo)
        status = None if read_error else read
    if result.get("ok"):
        result["message"] = f"AI review is {state} for {repo}."
        code = exit_code(status, None) if status else 0
    else:
        code = exit_code(result, result.get("error"))
    result["status"] = status
    if fmt == "json":
        emit_json(result)
    else:
        console.print(escape(result["message"]))
        if status:
            render_status(status)
    if code:
        raise SystemExit(code)


def _require_repo(repo_slug: str | None) -> str:
    repo = _repo_param(repo_slug)
    if repo is None:
        raise click.UsageError(
            "This checkout has no GitHub 'origin' remote. Pass --repo owner/name."
        )
    return repo


def bot_state(repo: str) -> tuple[dict[str, Any], str | None]:
    """``(state, error)``: the bot's part of *repo*'s setup status and ``None``,
    or the error object and its code (``signed_out``, ``unsupported``,
    ``offline``, ``http_<status>``), as :func:`fetch_setup_status` gives them."""
    payload, error = fetch_setup_status(repo)
    if error is not None:
        return {"repo": repo, **payload}, error
    steps = [
        step
        for step in payload.get("steps") or []
        if isinstance(step, dict) and step.get("id") in _BOT_STEPS
    ]
    bot = payload.get("bot") if isinstance(payload.get("bot"), dict) else {}
    state: dict[str, Any] = {
        "repo": repo,
        "account": payload.get("account"),
        "effective_tier": payload.get("effective_tier"),
        "plan": payload.get("plan"),
        "bot": bot,
        "coverage": payload.get("coverage"),
        "steps": steps,
        "next": _next(steps),
        "complete": bool(steps) and all(s.get("done") for s in steps if not s.get("optional")),
    }
    if "code" not in bot:
        state["note"] = _OLDER_SERVER
    return state, None


def _open(step: dict[str, Any]) -> bool:
    return not step.get("done") or step.get("code") not in _SETTLED | {None}


def _next(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first open bot step, required before optional, as ``{code, who, action, url}``."""
    open_steps = sorted((s for s in steps if _open(s)), key=lambda s: bool(s.get("optional")))
    if not open_steps:
        return None
    step = open_steps[0]
    return {key: step.get(key) for key in ("code", "who", "action", "url")}


def exit_code(payload: dict[str, Any], error: str | None) -> int:
    """3 when a person acts next, 1 on any other error, else 0."""
    if error is not None:
        return EXIT_NEEDS_HUMAN if error in _HUMAN_CODES else 1
    step = payload.get("next")
    return EXIT_NEEDS_HUMAN if isinstance(step, dict) and step.get("who") == "human" else 0


def _dollars(cents: Any) -> str:
    return f"${cents / 100:.2f}" if isinstance(cents, int | float) else "?"


def _rows(state: dict[str, Any]) -> list[tuple[str, str]]:
    """The state as label and value pairs; what the server left out is skipped."""
    bot = state.get("bot") or {}
    install = "not installed"
    if bot.get("installed"):
        install = "installed, not claimed" if bot.get("unclaimed") else "installed, claimed"
    rows = [("Install", install)]
    if bot.get("installed"):
        rows.append(("Reviews", "on" if bot.get("enabled") else "off"))
    ai = bot.get("ai") if isinstance(bot.get("ai"), dict) else None
    if ai:
        value = f"{'on' if ai.get('on') else 'off'} ({ai.get('code')})"
        if ai.get("depth"):
            value += f", depth {ai['depth']}"
        rows.append(("AI review", value))
    rows.append(("Plan", str(state.get("effective_tier") or "unknown")))
    credit = bot.get("credit") if isinstance(bot.get("credit"), dict) else None
    if credit:
        value = f"{_dollars(credit.get('balance_cents'))} left"
        if credit.get("cap_cents") is not None:
            value += (
                f", {_dollars(credit.get('spent_this_month_cents'))} of "
                f"{_dollars(credit['cap_cents'])} monthly limit spent"
            )
        rows.append(("Credit", value))
    last = bot.get("last_review") if isinstance(bot.get("last_review"), dict) else None
    if last:
        value = f"PR #{last.get('pr_number')}: {last.get('status')} ({last.get('tier')})"
        if last.get("check_url"):
            value += f"\n{last['check_url']}"
        rows.append(("Last review", value))
    if "coverage_received" in bot:
        rows.append(("Coverage", "received" if bot["coverage_received"] else "not received"))
    return [(label, escape(value)) for label, value in rows]


def render_status(state: dict[str, Any]) -> None:
    """The bot state for a person: one row per fact, then the next step."""
    from repowise.cli.ui.brand import key_value_table

    console.print(f"PR bot on [bold]{escape(state['repo'])}[/bold]")
    console.print(key_value_table(_rows(state), label_width=12))
    step = state.get("next")
    if step:
        who = "You" if step.get("who") == "human" else "Your agent"
        console.print(f"[bold]Next[/bold] ({who}): {escape(str(step.get('action') or ''))}")
        if step.get("url"):
            console.print(f"  [dim]{escape(str(step['url']))}[/dim]")
    elif state.get("complete"):
        console.print("[green]The PR bot is set up on this repo.[/green]")
    if state.get("note"):
        console.print(f"[dim]{escape(state['note'])}[/dim]")
