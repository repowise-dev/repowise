"""Signed-in agent CLIs: whether one is signed in, how it is billed, how long a run takes.

Claude Code, Codex and OpenCode can write documentation and answers on the
user's own login. The CLI's provider picker and the MCP server's upgrade line
both ask this module, so they cannot disagree about what is signed in.
"""

from __future__ import annotations

import importlib
import json
import math
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.agents.identity import CLAUDE_CODE, CODEX, OPENCODE, identity_for_provider
from repowise.core.platform.telemetry import network_disabled
from repowise.core.providers.llm.specs import PROVIDER_SPECS

if TYPE_CHECKING:
    from repowise.core.index_scope import FreeRoute

#: Agent-CLI providers, in picker order.
AGENT_PROVIDERS: tuple[str, ...] = tuple(
    spec.name
    for spec in sorted(PROVIDER_SPECS.values(), key=lambda s: s.picker_rank or 0)
    if spec.agent and spec.picker_rank is not None
)

# One page is one CLI process and a full model turn. A serialized 68-page run
# took about an hour (docs/agent/CLAUDE_CODE_PROVIDER.md), so a minute a call
# is the planning figure. Rough on purpose: it sizes the wait, not a deadline.
SECONDS_PER_CALL = 60


@dataclass(frozen=True)
class CliAuth:
    """How a signed-in agent CLI is billed, as far as its status command says.

    *billing* is what the user is told; it never claims the run is free.
    *offer* is False when the CLI reports no paid plan or no credentials: it
    stays selectable in the picker, but nothing recommends it. *plan_quota* is
    True only when the status names a paid plan whose quota the run draws on,
    the one case an upgrade line may call a no-spend route.
    """

    billing: str
    offer: bool = True
    plan_quota: bool = False


def _unknown(label: str) -> CliAuth:
    return CliAuth(f"{label} login; it runs on whatever that login is billed to")


def _claude_auth(out: str) -> CliAuth | None:
    try:
        data = json.loads(out)
    except ValueError:
        return _unknown("Claude Code")
    if not isinstance(data, dict) or not data.get("loggedIn"):
        return None
    backend = data.get("apiProvider")
    if backend and backend != "firstParty":
        return CliAuth(f"runs through {backend}: billed to that cloud account")
    return _claude_login_auth(data)


def _claude_login_auth(data: dict) -> CliAuth:
    """Billing for a first-party Claude Code login, by its ``authMethod``."""
    method = str(data.get("authMethod") or "")
    if method == "claude.ai":
        return _claude_plan_auth(data.get("subscriptionType"))
    if "key" in method.lower() or method == "console":
        return CliAuth("API key: billed per token")
    return _unknown("Claude Code")


def _claude_plan_auth(plan: object) -> CliAuth:
    if plan and plan != "free":
        return CliAuth(f"Claude {plan} plan: uses your plan quota", plan_quota=True)
    return CliAuth("signed in without a paid Claude plan", offer=False)


def _codex_auth(out: str) -> CliAuth | None:
    text = out.lower()
    if "not logged in" in text:
        return None
    if "chatgpt" in text:
        # The CLI cannot say whether that ChatGPT plan still includes Codex, and
        # a lapsed plan stays signed in, so this login is picker-only.
        return CliAuth(
            "signed in with ChatGPT: uses that plan's Codex quota, if the plan is active",
            offer=False,
        )
    if "api key" in text:
        return CliAuth("API key: billed per token")
    return _unknown("Codex CLI")


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _opencode_auth(out: str) -> CliAuth | None:
    found = re.search(r"(\d+)\s+credentials?", _ANSI.sub("", out))
    if found is None:
        return _unknown("OpenCode")
    if found.group(1) == "0":
        return CliAuth(
            "no stored credentials: runs on whatever provider its own config names",
            offer=False,
        )
    return CliAuth("runs on the provider its stored credentials name, billed there")


# Per agent: a status command that answers without a model call, and how to
# read it. OpenCode has no login query, but ``auth list`` counts credentials.
_AUTH_PROBES: dict[str, tuple[tuple[str, ...], Callable[[str], CliAuth | None]]] = {
    CLAUDE_CODE.slug: (CLAUDE_CODE.login_check, _claude_auth),
    CODEX.slug: (CODEX.login_check, _codex_auth),
    OPENCODE.slug: (("auth", "list"), _opencode_auth),
}


def agent_cli_auth(name: str) -> CliAuth | None:
    """How the agent CLI behind *name* is signed in; ``None`` when it is not."""
    agent = identity_for_provider(name)
    command = agent.which() if agent is not None else None
    if command is None:
        return None
    probe = _AUTH_PROBES.get(agent.slug)
    if probe is None:
        return None
    if network_disabled():
        # The probe runs a third-party CLI that may phone home. Installed stays
        # pickable by hand, but is never recommended on an unchecked sign-in.
        return CliAuth("sign-in not checked while network checks are off", offer=False)
    args, parse = probe
    try:
        # DEVNULL: an inherited Windows console stdin can block until the timeout.
        result = subprocess.run(
            [command, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return parse(f"{result.stdout}\n{result.stderr}".strip())


def process_limit(provider_name: str) -> int:
    """How many CLI processes *provider_name* runs at once."""
    spec = PROVIDER_SPECS[provider_name]
    cls = getattr(importlib.import_module(spec.module_path), spec.class_name)
    return int(cls.process_limit())


def _parallel(provider_name: str, concurrency: int | None) -> int:
    limit = process_limit(provider_name)
    return max(1, min(concurrency, limit) if concurrency else limit)


def subscription_minutes(
    provider_name: str, model_calls: int, concurrency: int | None = None
) -> int:
    """Minutes a subscription run of *model_calls* takes, at least one."""
    parallel = _parallel(provider_name, concurrency)
    return max(1, math.ceil(math.ceil(model_calls / parallel) * SECONDS_PER_CALL / 60))


def detect_free_route(repo_path: str | Path | None) -> FreeRoute | None:
    """The signed-in agent CLI billed to a plan quota, when no model is set up.

    ``None`` when a provider already resolves for *repo_path* (it has a model,
    so there is nothing to upgrade to) or when no agent CLI reports a paid
    plan. Probes in picker order and stops at the first plan-billed login, so
    the common machine runs one status command. ``minutes`` is left for the
    caller, which knows how many pages are unwritten.
    """
    from repowise.core.index_scope import FreeRoute
    from repowise.core.providers.llm.resolve import credentialed_providers, resolve_llm

    path = Path(repo_path) if repo_path else None
    keyed = [
        name
        for name in credentialed_providers(path)
        if name != "mock" and not getattr(PROVIDER_SPECS.get(name), "agent", False)
    ]
    if resolve_llm(path, autodetect=False).provider or keyed:
        return None
    for name in AGENT_PROVIDERS:
        auth = agent_cli_auth(name)
        agent = identity_for_provider(name)
        if auth is not None and auth.plan_quota and agent is not None:
            return FreeRoute(provider=name, cli_name=agent.display_name)
    return None


def route_minutes(route: FreeRoute | None, model_calls: int) -> FreeRoute | None:
    """*route* with the minutes *model_calls* take on it; unchanged without calls."""
    if route is None or model_calls <= 0:
        return route
    return replace(route, minutes=subscription_minutes(route.provider, model_calls))
