"""Own-subscription providers: a logged-in Claude Code, Codex or OpenCode CLI.

Detected and offered, never picked on the user's behalf: the picker shows them
with a time and quota estimate, and a run that writes no documentation names
the one it found. Detection lives here so the picker, that note and the init
plan screen all ask the same question.
"""

from __future__ import annotations

import importlib
import json
import math
import re
import subprocess
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from repowise.core.agents.identity import CLAUDE_CODE, CODEX, OPENCODE, identity_for_provider
from repowise.core.providers.llm.specs import PROVIDER_SPECS

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
class SubscriptionContext:
    """What the picker knows about the run that opened it.

    *prefer*: agent CLIs set up in this repo, the first ready one becomes the
    default (see ``agent_providers_set_up``). *model_calls* and *concurrency*
    size the quota and time estimate; no estimate is shown without calls.
    *allow_skip*: a provider that is not set up offers to go on without a
    model, and the picker returns ``None`` for that answer.
    """

    prefer: tuple[str, ...] = ()
    model_calls: int | None = None
    concurrency: int = 10
    allow_skip: bool = False


@dataclass(frozen=True)
class CliAuth:
    """How a signed-in agent CLI is billed, as far as its status command says.

    *billing* is what the user is told; it never claims the run is free.
    *offer* is False when the CLI reports no paid plan or no credentials: it
    stays selectable in the picker, but nothing recommends it.
    """

    billing: str
    offer: bool = True


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
        return CliAuth(f"Claude {plan} plan: uses your plan quota")
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


def agent_cli_status(name: str) -> tuple[bool, bool]:
    """``(installed, signed in)`` for the agent CLI behind provider *name*."""
    agent = identity_for_provider(name)
    if agent is None or not agent.is_installed():
        return False, False
    return True, agent_cli_auth(name) is not None


def logged_in_agent_providers() -> dict[str, CliAuth]:
    """Signed-in agent CLIs and how each is billed, in picker order.

    Each status check is a short subprocess, so they run side by side.
    """
    with ThreadPoolExecutor(max_workers=len(AGENT_PROVIDERS) or 1) as pool:
        auths = list(pool.map(agent_cli_auth, AGENT_PROVIDERS))
    return {
        name: auth for name, auth in zip(AGENT_PROVIDERS, auths, strict=True) if auth is not None
    }


def ready_agent_providers() -> tuple[str, ...]:
    """Signed-in agent CLIs worth offering, in picker order (Claude Code first)."""
    return tuple(name for name, auth in logged_in_agent_providers().items() if auth.offer)


def process_limit(provider_name: str) -> int:
    """How many CLI processes *provider_name* runs at once."""
    spec = PROVIDER_SPECS[provider_name]
    cls = getattr(importlib.import_module(spec.module_path), spec.class_name)
    return int(cls.process_limit())


def model_call_count(plans: Iterable[object]) -> int:
    """Model calls a generation plan makes: one per page a model writes."""
    from repowise.core.cost_estimator import STRUCTURAL_PAGE_TYPES

    return sum(
        int(getattr(plan, "count", 0))
        for plan in plans
        if getattr(plan, "page_type", None) not in STRUCTURAL_PAGE_TYPES
    )


def _parallel(provider_name: str, concurrency: int | None) -> int:
    limit = process_limit(provider_name)
    return max(1, min(concurrency, limit) if concurrency else limit)


def subscription_minutes(
    provider_name: str, model_calls: int, concurrency: int | None = None
) -> int:
    """Minutes a subscription run of *model_calls* takes, at least one."""
    parallel = _parallel(provider_name, concurrency)
    return max(1, math.ceil(math.ceil(model_calls / parallel) * SECONDS_PER_CALL / 60))


def subscription_quota(
    provider_name: str,
    model_calls: int,
    concurrency: int | None = None,
    billing: str | None = None,
) -> str:
    """``uses your Claude Code login, about 14 model calls, 4 at a time``."""
    parallel = _parallel(provider_name, concurrency)
    calls = "call" if model_calls == 1 else "calls"
    return (
        f"{billing or PROVIDER_SPECS[provider_name].note}, about {model_calls:,} model {calls}, "
        f"{parallel} at a time"
    )


def subscription_estimate(
    provider_name: str,
    model_calls: int,
    concurrency: int | None = None,
    billing: str | None = None,
) -> str:
    """``uses your Claude Code login, about 14 model calls, 4 at a time, about 4 min``.

    What a subscription run costs is quota and time, not dollars, so this is
    what stands where an API provider shows its price. *concurrency* is the
    run's ``--concurrency``; the CLI's own process limit caps it. *billing*
    (from :func:`agent_cli_auth`) replaces the generic login note.
    """
    minutes = subscription_minutes(provider_name, model_calls, concurrency)
    quota = subscription_quota(provider_name, model_calls, concurrency, billing)
    return f"{quota}, about {minutes:,} min"


def subscription_note(ready: tuple[str, ...], auth: CliAuth | None = None) -> str | None:
    """One line naming the first ready subscription CLI and the command that uses it.

    For a run that writes no documentation with a model: it never switches
    the run to the CLI, it says how to and how that CLI is billed. *auth* is
    probed when not given.
    """
    if not ready:
        return None
    name = ready[0]
    agent = identity_for_provider(name)
    if agent is None:
        return None
    auth = auth or agent_cli_auth(name)
    billing = auth.billing if auth is not None else PROVIDER_SPECS[name].note
    return (
        f"{agent.display_name} is signed in ({billing}). Run [bold]repowise update "
        f"--full --provider {name}[/bold] to have it write the documentation."
    )


__all__ = [
    "AGENT_PROVIDERS",
    "SECONDS_PER_CALL",
    "CliAuth",
    "SubscriptionContext",
    "agent_cli_auth",
    "agent_cli_status",
    "logged_in_agent_providers",
    "model_call_count",
    "process_limit",
    "ready_agent_providers",
    "subscription_estimate",
    "subscription_minutes",
    "subscription_note",
    "subscription_quota",
]
