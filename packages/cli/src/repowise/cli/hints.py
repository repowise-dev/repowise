"""One quiet line about repowise.dev, at the moments it would help.

Rules that keep it from being a nag:

* stderr only, one dim line plus its link, never in ``--format json`` runs;
* at most one hint per command run, and each hint at most once every 7 days;
* never from ``mcp``, ``serve``, ``watch``, hooks, or anything an agent reads;
* off in CI and tests, when stderr is not a terminal, for signed-in users,
  with ``REPOWISE_NO_HINTS=1``, ``repowise config hints off`` or ``DO_NOT_TRACK``.

Deciding never touches the network: everything is read from local files and
the environment, so a hint can never slow a command down. Every local feature
works without signing in; a hint only says what the hosted side adds.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_SHOWN_KEY = "hints_shown"
_ENABLED_KEY = "hints_enabled"
_EVERY = timedelta(days=7)

#: Commands whose output belongs to an agent, a daemon or a hook.
_QUIET_COMMANDS = frozenset({"mcp", "serve", "watch", "hook", "augment"})

#: Set once a hint is shown, so a run never shows two.
_shown_this_run = False


@dataclass(frozen=True)
class Hint:
    text: str
    #: Where the link was shown, for the site's attribution.
    src: str
    #: The section of the repowise.dev/hosted story the link opens.
    moment: str
    #: The free next step the line ends with.
    action: str = "Publish it free: repowise publish"


#: Publishing a public repo is free (no card, up to 2 repos); anything about
#: private repos or more repos says "free for 10 days, card required".
HINTS: dict[str, Hint] = {
    "init_success": Hint(
        "Use this repo from Claude.ai or ChatGPT, or share it with your team.",
        src="cli_init_success",
        moment="mcp",
    ),
    "provider_fail": Hint(
        "Don't want to set up a key? repowise.dev writes the docs for you.",
        src="cli_provider_fail",
        moment="keys",
        action="Start free: repowise publish",
    ),
    "interrupt": Hint(
        "Big repo? repowise.dev indexes it in the cloud and keeps it fresh on every push.",
        src="cli_interrupt",
        moment="sync",
        action="Start free: repowise publish",
    ),
    "large_repo": Hint(
        "repowise.dev keeps big repos fresh automatically on every push "
        "(Pro: free for 10 days, card required).",
        src="cli_large_repo",
        moment="sync",
    ),
    "slow_update": Hint(
        "Skip manual updates: repowise.dev re-indexes on every push.",
        src="cli_slow_update",
        moment="sync",
    ),
    "ask_nokey": Hint(
        "No LLM key set. Sign in to repowise.dev for 10 free answers a month.",
        src="cli_ask_nokey",
        moment="keys",
        action="Start free: repowise publish",
    ),
}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def set_enabled(enabled: bool) -> None:
    """``repowise config hints on|off``."""
    from repowise.cli.platform import store

    store.update(**{_ENABLED_KEY: bool(enabled)})


def is_enabled() -> bool:
    """The user's switch and the environment, nothing else."""
    if _env_truthy("REPOWISE_NO_HINTS") or _env_truthy("DO_NOT_TRACK"):
        return False
    from repowise.cli.platform import store

    return store.load().get(_ENABLED_KEY) is not False


def _quiet_command() -> bool:
    try:
        import click

        ctx = click.get_current_context(silent=True)
    except Exception:
        return False
    if ctx is None:
        return False
    root = ctx.find_root()
    names = {root.invoked_subcommand, ctx.info_name}
    return bool(names & _QUIET_COMMANDS)


def _signed_in() -> bool:
    from repowise.cli.platform import credentials

    creds = credentials.load()
    return bool(creds) and not creds.get("stale")


def _due(hint_id: str, now: datetime) -> bool:
    from repowise.cli.platform import store

    last = (store.load().get(_SHOWN_KEY) or {}).get(hint_id)
    if not isinstance(last, str):
        return True
    try:
        return now - datetime.fromisoformat(last) >= _EVERY
    except ValueError:
        return True


def _mark_shown(hint_id: str, now: datetime) -> None:
    from repowise.cli.platform import store

    shown = dict(store.load().get(_SHOWN_KEY) or {})
    shown[hint_id] = now.isoformat()
    store.update(**{_SHOWN_KEY: shown})


def should_show(hint_id: str, *, fmt: str = "text") -> bool:
    if _shown_this_run or hint_id not in HINTS or fmt == "json":
        return False
    from repowise.cli.platform.telemetry.environment import is_ci

    if is_ci() or not is_enabled() or _quiet_command():
        return False
    import sys

    if not sys.stderr.isatty():
        return False
    return not _signed_in() and _due(hint_id, datetime.now(UTC))


def maybe_hint(hint_id: str, *, fmt: str = "text") -> bool:
    """Show the hint on stderr if every rule above allows it. Never raises.

    Returns whether it was shown.
    """
    global _shown_this_run
    try:
        if not should_show(hint_id, fmt=fmt):
            return False
        hint = HINTS[hint_id]
        from rich.markup import escape

        from repowise.cli.helpers import err_console
        from repowise.cli.platform.links import site_link

        url = site_link("hosted", hint.src, fragment=hint.moment)
        # The URL is the link's own text, so a terminal without hyperlinks
        # still shows something to copy.
        err_console.print(
            f"\n  [dim]{escape(hint.text)} {escape(hint.action)}\n"
            f"  See how: [link={url}]{escape(url)}[/link][/dim]"
        )
        _shown_this_run = True
        _mark_shown(hint_id, datetime.now(UTC))
        with contextlib.suppress(Exception):
            from repowise.cli.platform import telemetry

            telemetry.add_command_outcome(hint_shown=hint_id)
        return True
    except Exception:
        return False
