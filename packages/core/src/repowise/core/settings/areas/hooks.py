"""``hooks:`` in ``.repowise/config.yaml``: opt-in agent hook surfaces.

Read by ``hook_flag_enabled`` in the CLI, which this package cannot import, so
the flag names are declared here and a parity test holds them to every
``CONFIG_FLAG`` and ``hook_flag_enabled(..., "<flag>")`` in the CLI source.
``REPOWISE_HOOK_<FLAG>`` overrides a flag for one session.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from repowise.core.settings.model import TRUTHY, Env, Proposal, Setting

AREA = "hooks"

#: A replacing surface is suggested once the hook has measured, while the
#: surface was off, at least this many items and tokens it would have saved
#: over :data:`repowise.core.settings.suggest.MEASURE_DAYS`. Below that the
#: saving is too small to be worth a change in what the agent sees.
MIN_FORGONE_ITEMS = 10
MIN_FORGONE_TOKENS = 25_000
#: Recent agent sessions whose repowise hooks ran in this repository.
MIN_AGENT_SESSIONS = 3

#: Per replacing surface: what the agent did, and what the replacement keeps.
_FORGONE_WORDS: dict[str, tuple[str, str]] = {
    "read_skeleton": (
        "agents here read {n} large files whole; their outlines",
        "The outline keeps every signature and the line ranges to read any body back.",
    ),
    "search_digest": (
        "{n} agent searches here flooded many files; a digest",
        "The digest names every file and its match count, so the agent can narrow the search.",
    ),
    "read_reread": (
        "agents here re-read {n} unchanged files; a short notice",
        "Reading the file a second time still returns it whole.",
    ),
}


def _forgone(name: str) -> Callable[[Any], Proposal | None]:
    """On, when the hook measured a clear saving this surface would have made."""
    did, keeps = _FORGONE_WORDS[name]

    def suggest(facts: Any) -> Proposal | None:
        items, tokens = facts.forgone.get(name, (0, 0))
        if items < MIN_FORGONE_ITEMS or tokens < MIN_FORGONE_TOKENS:
            return None
        return Proposal(
            True,
            f"In the last {facts.measure_days} days {did.format(n=f'{items:,}')} "
            f"would have cost {tokens:,} fewer tokens.",
            f"About {tokens:,} fewer agent tokens a month. {keeps}",
            "tokens",
        )

    return suggest


def _coverage_reingest(facts: Any) -> Proposal | None:
    """On, when coverage is stored and agents' repowise hooks ran here lately."""
    sessions = facts.agent_sessions
    if not facts.coverage_ingested or sessions < MIN_AGENT_SESSIONS:
        return None
    return Proposal(
        True,
        f"Coverage reports are stored for this repository and {sessions} agent sessions "
        f"ran repowise hooks here in the last {facts.session_days} days, but coverage "
        "is only re-read on repowise update.",
        "Health and change-risk answers use the coverage of the agent's latest full "
        "test run. Codex picks it up at once, Claude Code after the next repowise update.",
        "quality",
    )


_SUGGEST: dict[str, Callable[[Any], Proposal | None]] = {
    **{name: _forgone(name) for name in _FORGONE_WORDS},
    "coverage_reingest": _coverage_reingest,
}

#: Every flag ``hook_flag_enabled`` is asked about. All are off when absent.
_FLAGS: dict[str, str] = {
    "read_skeleton": "Serve a whole-file Read of a large indexed file as its outline.",
    "read_reread": "Serve an unchanged repeat Read as a short notice.",
    "search_digest": "Serve a search that floods many files as a digest.",
    "coverage_reingest": "Re-read coverage reports after an agent's full test run.",
    "measure": "On update, check locally whether agents acted on each hook firing.",
    "health_gate": "Block an agent's commit or push that fails repowise health check.",
}


def _flag(name: str, description: str) -> Setting:
    return Setting(
        key=f"hooks.{name}",
        type="bool",
        default=False,
        description=description,
        area=AREA,
        env=Env(f"REPOWISE_HOOK_{name.upper()}", wins=True, true_values=TRUTHY),
        suggest_when=_SUGGEST.get(name),
    )


SETTINGS: tuple[Setting, ...] = (
    *(_flag(name, description) for name, description in _FLAGS.items()),
    Setting(
        key="hooks.read_skeleton_mode",
        type="str",
        default="smart",
        description="What a served outline keeps: smart, or plus to keep all code "
        "outside function bodies.",
        area=AREA,
        choices=("smart", "plus"),
        env=Env("REPOWISE_HOOK_READ_SKELETON_MODE", wins=True),
        parser="repowise.core.settings.parsers:folded",
    ),
    Setting(
        key="REPOWISE_HOOK_UPDATED_OUTPUT",
        type="bool",
        default=None,
        description="Force whether the Claude Code hook may replace a tool's output; "
        "unset decides from the client version.",
        area=AREA,
        scope="env",
        env=Env("REPOWISE_HOOK_UPDATED_OUTPUT", true_values=TRUTHY),
        internal=True,
    ),
)
