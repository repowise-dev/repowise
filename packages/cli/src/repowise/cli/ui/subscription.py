"""Own-subscription providers: a logged-in Claude Code, Codex or OpenCode CLI.

Detected and offered, never picked on the user's behalf: the picker shows them
with a time and quota estimate, and a run that writes no documentation names
the one it found. Detection lives in
:mod:`repowise.core.providers.llm.subscription`, so the picker, that note, the
init plan screen and the MCP server all ask the same question.
"""

from __future__ import annotations

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.agents.identity import identity_for_provider
from repowise.core.providers.llm.specs import PROVIDER_SPECS
from repowise.core.providers.llm.subscription import (
    AGENT_PROVIDERS,
    SECONDS_PER_CALL,
    CliAuth,
    _parallel,
    agent_cli_auth,
    process_limit,
    subscription_minutes,
)

if TYPE_CHECKING:
    from repowise.core.index_scope import FreeRoute


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


def model_call_count(plans: Iterable[object]) -> int:
    """Model calls a generation plan makes: one per page a model writes."""
    from repowise.core.cost_estimator import STRUCTURAL_PAGE_TYPES

    return sum(
        int(getattr(plan, "count", 0))
        for plan in plans
        if getattr(plan, "page_type", None) not in STRUCTURAL_PAGE_TYPES
    )



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


def subscription_note(
    ready: tuple[str, ...], command: str, auth: CliAuth | None = None
) -> str | None:
    """One line naming the first ready subscription CLI and *command* run with it.

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
        f"{agent.display_name} is signed in ({billing}). Run [bold]{command} "
        f"--provider {name}[/bold] to have it write the documentation."
    )


def upgrade_route(repo_path: Path) -> FreeRoute | None:
    """The plan-billed agent CLI ``repo_path`` can add a model with, timed by ``generate``'s calls.

    ``None`` when a provider already resolves or no CLI on a paid plan is
    signed in. The time is left out when the call count cannot be read.
    """
    from repowise.core.providers.llm.subscription import detect_free_route, route_minutes

    route = detect_free_route(repo_path)
    if route is None:
        return None
    from repowise.cli.helpers import run_async

    try:
        calls = run_async(_generate_calls(repo_path))
    except Exception:
        return route
    return route_minutes(route, calls)


async def _generate_calls(repo_path: Path) -> int:
    from repowise.cli.helpers import get_db_url_for_repo
    from repowise.core.generation.scope import generate_model_calls
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_repository_by_path,
        get_session,
    )

    engine = create_engine(get_db_url_for_repo(repo_path))
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            return await generate_model_calls(session, repo.id, repo_path) if repo else 0
    finally:
        await engine.dispose()


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
    "upgrade_route",
]
