"""What the init plan screen shows: the facts, gathered before it renders."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from repowise.cli.ui.mode_names import model_name
from repowise.cli.ui.repo_scanner import (
    RepoScanInfo,
    estimated_model_minutes,
    estimated_run_seconds,
    source_file_counts,
)


@dataclass(frozen=True)
class ModelPrice:
    """A model and what a written run with it costs: money or quota, and time."""

    provider: str
    model: str
    #: ``about $0.04-$0.07, about 14 model calls``, or a subscription's quota line.
    cost: str
    minutes: int

    @property
    def model_label(self) -> str:
        """The model id without a repeated ``<provider>/`` prefix."""
        return model_name(self.provider, self.model) or self.model


@dataclass(frozen=True)
class ModelOffer:
    """The model ``m`` defaults to. ``price`` is ``None`` when nothing is set up."""

    price: ModelPrice | None = None
    #: How it was found: ``OPENAI_API_KEY found``, ``Claude Code is logged in``.
    found: str = ""


@dataclass
class PlanState:
    """Everything one render of the plan screen needs."""

    repo_name: str
    scan: RepoScanInfo
    #: ``standard`` or ``fast`` (index only, no documentation).
    run_mode: str = "standard"
    #: The model picked with ``m``; Enter then runs it.
    chosen: ModelPrice | None = None
    offer: ModelOffer = field(default_factory=ModelOffer)
    #: Embedder each path would use; ``mock`` means full-text search only.
    free_embedder: str = "mock"
    model_embedder: str = "mock"
    #: Display names of the agents the run connects.
    agents: tuple[str, ...] = ()
    editor_setup: bool = True
    sync: bool = True
    #: Old and new target of the global MCP entry; empty when it does not move.
    outside: tuple[str, ...] = ()
    this_repo_only: bool = False
    large_repo: bool = False
    #: Machine-specific files git would see, and whether Enter excludes them.
    keep_out: tuple[str, ...] = ()
    git_exclude: bool = False
    #: Commits git history walks at most (``--commit-limit`` or config.yaml).
    commit_limit: int = 5000

    @property
    def can_keep_global(self) -> bool:
        """Whether ``s`` means anything: the global entry would move."""
        return self.editor_setup and bool(self.outside)


@dataclass(frozen=True)
class RunShape:
    """What sizes a written run before indexing: the scan and two run options."""

    scan: RepoScanInfo
    onboarding: bool = True
    concurrency: int = 10
    #: The other repos of a workspace run, each planned as its own repo.
    also: tuple[RepoScanInfo, ...] = ()


def _plans(shape: RunShape) -> list:
    from repowise.core.cost_estimator import approximate_generation_plan

    return [
        plan
        for scan in (shape.scan, *shape.also)
        if scan.total_files
        for plan in approximate_generation_plan(scan.total_files, onboarding=shape.onboarding)
    ]


def model_calls_for(shape: RunShape) -> int:
    """Model calls a written run makes, from the plan the server quotes before indexing."""
    from repowise.cli.ui.subscription import model_call_count

    return model_call_count(_plans(shape))


def price_model(provider: str, model: str, shape: RunShape) -> ModelPrice:
    """What a written run with *provider* / *model* costs, before indexing."""
    from repowise.cli.ui.subscription import subscription_minutes, subscription_quota
    from repowise.core.cost_estimator import estimate_cost
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    calls = model_calls_for(shape)
    spec = PROVIDER_SPECS.get(provider)
    if spec is not None and spec.agent:
        return ModelPrice(
            provider,
            model,
            subscription_quota(provider, calls, shape.concurrency),
            subscription_minutes(provider, calls, shape.concurrency),
        )
    est = estimate_cost(_plans(shape), provider, model)
    rng = est.cost_range
    price = f"about ${rng.low:.2f}-${rng.high:.2f}" if rng else f"about ${est.estimated_cost_usd:.2f}"
    calls_word = "call" if calls == 1 else "calls"
    return ModelPrice(
        provider,
        model,
        f"{price}, about {calls:,} model {calls_word}",
        estimated_model_minutes(calls),
    )


def _found_phrase(provider: str, how: str) -> str:
    from repowise.core.agents.identity import identity_for_provider

    agent = identity_for_provider(provider)
    if agent is not None:
        label = agent.display_name.removesuffix(" CLI")
        return f"{label} is logged in" if agent.login_check else f"{label} is installed"
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    spec = PROVIDER_SPECS.get(provider)
    if spec is not None and spec.local:
        return f"{spec.label.removesuffix(' (Local)')} is running at {how}"
    return f"{how} found" if how else f"{provider} is set up"


def build_offer(detected: dict[str, str], prefer: tuple[str, ...], shape: RunShape) -> ModelOffer:
    """The provider ``m`` defaults to: an agent CLI set up here, else the first detected.

    *detected* is the picker's own detection (``provider -> how``) and
    *prefer* its preference, so this line and the picker default agree.
    """
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    if not detected:
        return ModelOffer()
    name = next((p for p in prefer if p in detected), next(iter(detected)))
    runtime = "openai" if name == "openai_compatible" else name
    spec = PROVIDER_SPECS.get(runtime)
    model = spec.default_model if spec is not None else ""
    return ModelOffer(
        price=price_model(runtime, model, shape),
        found=_found_phrase(runtime, detected[name]),
    )


def eta_seconds(state: PlanState) -> tuple[float, float]:
    """Low and high seconds for the run as planned."""
    low, high = estimated_run_seconds(state.scan, state.commit_limit)
    if state.chosen is not None:
        low, high = low + state.chosen.minutes * 60, high + state.chosen.minutes * 60
    return low, high


def eta_phrase(low: float, high: float) -> str:
    """``under a minute``, ``about 1-2 min`` or ``about 3-6 min``."""
    if high < 60:
        return "under a minute"
    lo = max(1, math.floor(low / 60))
    hi = max(lo + 1, math.ceil(high / 60))
    return f"about {lo:,}-{hi:,} min"


def source_and_languages(scan: RepoScanInfo) -> tuple[int, int]:
    """``(source files, source languages)`` from the pre-scan."""
    counts = {lang: n for lang, n in source_file_counts(scan).items() if n > 0}
    return sum(counts.values()), len(counts)
