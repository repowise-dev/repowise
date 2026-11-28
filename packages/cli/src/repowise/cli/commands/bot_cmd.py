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
from repowise.cli.exit_codes import EXIT_DEGRADED, EXIT_NEEDS_HUMAN
from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option

_EXIT_HELP = (
    "Exit codes: 0 nothing waits on a person; 3 a person's step is next (sign in, "
    "install, claim, approve or pay: 'next' says who, what and where); 1 error "
    "(offline, refused, or repowise.dev does not offer this yet); 2 'ai' made the "
    "change but could not read the state after it."
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
      next      {code, who, action, url, optional}, or null when nothing
                is left
      complete  true once PR reviews are on
    """
    repo = _require_repo(repo_slug, fmt)
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
    'repowise bot status' after the change, or null when it cannot be read
    (then the exit is 2: the change was made, the state after it is unknown).
    """
    if depth and state == "off":
        _usage_error(fmt, "bad_usage", "--depth goes with 'repowise bot ai on'.")
    repo = _require_repo(repo_slug, fmt)
    config = {"review_mode": "ai" if state == "on" else "signals"}
    if depth:
        config["review_depth"] = depth
    result, status = _set_review_mode(repo, config)
    if result.get("ok"):
        result["message"] = f"AI review is {state} for {repo}."
    _report(result, status, fmt)


def _report(result: dict[str, Any], status: dict[str, Any] | None, fmt: str) -> None:
    """Print a bot action's outcome and the state after it; exit as :func:`exit_code` says."""
    if fmt == "json":
        emit_json(result)
    else:
        console.print(escape(result["message"]))
        if status:
            render_status(status)
    if not result.get("ok"):
        code = exit_code(result, result.get("error"))
    else:
        # The change landed but the state after it is unknown: degraded, not clean.
        code = exit_code(status, None) if status else EXIT_DEGRADED
    if code:
        raise SystemExit(code)


def _set_review_mode(
    repo: str, config: dict[str, str]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Patch the repo's review mode; the outcome, with the state read after it
    as ``status`` (None when the state cannot be read)."""
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
    result["status"] = status
    return result, status


def _require_repo(repo_slug: str | None, fmt: str) -> str:
    """The repo to act on; with none, a usage error (one JSON object under json)."""
    repo = _repo_param(repo_slug)
    if repo is not None:
        return repo
    _usage_error(
        fmt, "no_repo", "This checkout has no GitHub 'origin' remote. Pass --repo owner/name."
    )
    raise AssertionError  # unreachable: _usage_error always exits


def _usage_error(fmt: str, code: str, message: str) -> None:
    """A usage error: one JSON object and exit 1 under json, else Click's usage exit 2."""
    if fmt == "json":
        emit_json({"error": code, "message": message})
        raise SystemExit(1)
    raise click.UsageError(message)


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
    bot = _dict(payload.get("bot"))
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
    if steps and "code" not in bot:
        state["note"] = _OLDER_SERVER
    return state, None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _open(step: dict[str, Any]) -> bool:
    return not step.get("done") or step.get("code") not in _SETTLED | {None}


def _next(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first open bot step, required before optional, as
    ``{code, who, action, url, optional}``."""
    open_steps = sorted((s for s in steps if _open(s)), key=lambda s: bool(s.get("optional")))
    if not open_steps:
        return None
    step = open_steps[0]
    return {key: step.get(key) for key in ("code", "who", "action", "url")} | {
        "optional": bool(step.get("optional"))
    }


def exit_code(payload: dict[str, Any], error: str | None) -> int:
    """3 when a person has to act before the bot works as asked, 1 on any other
    error, else 0. An optional person's step (merging the coverage PR) is named
    in ``next`` but does not hold an agent up."""
    if error is not None:
        return EXIT_NEEDS_HUMAN if error in _HUMAN_CODES else 1
    step = _dict(payload.get("next"))
    return EXIT_NEEDS_HUMAN if step.get("who") == "human" and not step.get("optional") else 0


def _dollars(cents: Any) -> str:
    return f"${cents / 100:.2f}" if isinstance(cents, int | float) else "?"


def _install_rows(bot: dict[str, Any]) -> list[tuple[str, str]]:
    if not bot.get("installed"):
        return [("Install", "not installed")]
    claim = "not claimed" if bot.get("unclaimed") else "claimed"
    return [
        ("Install", f"installed, {claim}"),
        ("Reviews", "on" if bot.get("enabled") else "off"),
    ]


def _ai_rows(bot: dict[str, Any]) -> list[tuple[str, str]]:
    ai = _dict(bot.get("ai"))
    if not ai:
        return []
    value = f"{'on' if ai.get('on') else 'off'} ({ai.get('code')})"
    if ai.get("depth"):
        value += f", depth {ai['depth']}"
    return [("AI review", value)]


def _credit_rows(bot: dict[str, Any]) -> list[tuple[str, str]]:
    credit = _dict(bot.get("credit"))
    if not credit:
        return []
    value = f"{_dollars(credit.get('balance_cents'))} left"
    if credit.get("cap_cents") is not None:
        value += (
            f", {_dollars(credit.get('spent_this_month_cents'))} of "
            f"{_dollars(credit['cap_cents'])} monthly limit spent"
        )
    return [("Credit", value)]


def _review_rows(bot: dict[str, Any]) -> list[tuple[str, str]]:
    rows = []
    last = _dict(bot.get("last_review"))
    if last:
        value = f"PR #{last.get('pr_number')}: {last.get('status')} ({last.get('tier')})"
        if last.get("check_url"):
            value += f"\n{last['check_url']}"
        rows.append(("Last review", value))
    if "coverage_received" in bot:
        rows.append(("Coverage", "received" if bot["coverage_received"] else "not received"))
    return rows


def _rows(state: dict[str, Any]) -> list[tuple[str, str]]:
    """The state as label and value pairs; what the server left out is skipped."""
    bot = _dict(state.get("bot"))
    rows = [
        *_install_rows(bot),
        *_ai_rows(bot),
        ("Plan", str(state.get("effective_tier") or "unknown")),
        *_credit_rows(bot),
        *_review_rows(bot),
    ]
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
