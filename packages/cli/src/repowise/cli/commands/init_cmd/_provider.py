"""Provider stage of ``repowise init``: which model, if any, this run calls."""

from __future__ import annotations

import sys
from typing import Any

import click

from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import console, resolve_explicit_provider_or_prompt, run_async
from repowise.cli.providers import resolve_embedder_choice, template_run_embedder
from repowise.cli.ui import MODEL, OK, VALUE, WARN
from repowise.cli.ui.glyphs import glyph
from repowise.cli.ui.mascot import owl_status
from repowise.cli.ui.mode_names import BUILT_FROM_CODE, INDEX_ONLY, model_name, written_by
from repowise.core.index_scope import MODEL_COMMAND

from ._options import InitRun


def announce_index_only(run: InitRun) -> None:
    """Tell the user what a run without a model builds.

    An interactive run already showed it on the plan screen, so nothing is
    printed after it.
    """
    fast = run.opts.run_mode == "fast"
    if not run.is_interactive:
        mode = INDEX_ONLY if fast else BUILT_FROM_CODE
        console.print(f"[bold]repowise init[/bold] · {mode} · {run.repo_path}")
        if fast:
            console.print(
                "Indexing the graph and git history [dim](no documentation, no spend).[/dim]"
            )
        else:
            console.print("Building documentation from code [dim](no model, no spend).[/dim]")


def _resolve_or_fall_back(run: InitRun) -> None:
    """Resolve the docs provider, or fall back to the template wiki when none is set.

    An explicit provider may onboard its missing key (and, for the
    OpenAI-compatible adapter, its endpoint) here when stdin is a real
    terminal. Auto-detection and scripted runs remain non-interactive: a --yes
    run with no key lands in the template wiki rather than in a question or a
    crash.
    """
    opts = run.opts
    try:
        run.provider = resolve_explicit_provider_or_prompt(
            opts.provider_name,
            opts.model,
            run.repo_path,
            interactive=sys.stdin.isatty() and not opts.yes and not opts.index_only,
            save_key=opts.save_key,
        )
    except click.ClickException as exc:
        # Nothing configured anywhere. Workspace init, workspace update and the
        # OSS server all render a template wiki here rather than refusing to
        # run (#999). An explicitly named --provider is a different question:
        # the user asked for it by name, so the failure is the real answer.
        if opts.provider_name is not None:
            raise
        run.no_provider = True
        if not run.is_interactive:
            console.print(f"[bold]repowise init[/bold] · {BUILT_FROM_CODE} · {run.repo_path}")
        console.print(
            f"[{WARN}]No model configured.[/] Building documentation from "
            "code instead (no key, no spend).\n"
            f"[dim]Set a key (or pass --provider) and run [bold]{MODEL_COMMAND}"
            "[/bold] to have a model write it.[/dim]"
        )
        # A provider that was configured but could not be built is a different
        # problem from none being configured, and the message above would send
        # the user to set a key they have already set.
        if "Could not set up" in str(exc):
            console.print(f"  [dim]{exc}[/dim]")


def note_subscription_cli(run: InitRun) -> None:
    """Name a logged-in Claude Code / Codex / OpenCode on a run no model writes.

    Never switches the run to it: a subscription is spent only when asked for
    with --provider. Interactive runs got the picker instead, and a configured
    provider is already a choice.
    """
    if not _wants_subscription_note(run):
        return
    from repowise.cli.ui.subscription import logged_in_agent_providers, subscription_note

    offered = {n: auth for n, auth in logged_in_agent_providers().items() if auth.offer}
    if not offered:
        return
    note = subscription_note(tuple(offered), next(iter(offered.values())))
    if note:
        console.print(f"  [dim]{note}[/dim]")


def _wants_subscription_note(run: InitRun) -> bool:
    """A scripted run nobody picked a provider for, and no model writes."""
    from repowise.core.platform.telemetry import network_disabled

    if network_disabled():  # the probe spawns third-party CLIs that phone home
        return False
    chosen = run.is_interactive or run.opts.provider_name or run.config.get("provider")
    return not chosen and bool(run.opts.index_only or run.no_provider)


def _print_provider_summary(run: InitRun) -> None:
    provider = run.provider
    if not run.is_interactive and not run.no_provider:
        mode = written_by(
            model_name(provider.provider_name, provider.model_name) if provider else None
        )
        console.print(f"[bold]repowise init[/bold] · {mode} · {run.repo_path}")
    if provider is not None:
        shown = model_name(provider.provider_name, provider.model_name)
        console.print(
            f"  Provider: [{VALUE}]{provider.provider_name}[/] / Model: [{MODEL}]{shown}[/]"
        )
    console.print(f"  Embedder: [{VALUE}]{_stated_embedder(run)}[/]")
    if run.language != "en":
        console.print(f"  Language: [{VALUE}]{run.language}[/]")
    if run.resolved_reasoning != "auto":
        console.print(f"  Reasoning: [{VALUE}]{run.resolved_reasoning}[/]")


def _stated_embedder(run: InitRun) -> str:
    """The embedder this run will use and why, e.g. ``gemini (GEMINI_API_KEY is set)``."""
    name, reason = run.embedder_name_resolved, run.embedder_reason
    if run.provider is None:
        # The template wiki keeps a free run free; the generation phase says so.
        keyless = template_run_embedder(name, run.embedder_was_requested)
        if keyless != name:
            name, reason = keyless, "keyless run"
    if name == "mock":
        return f"mock ({reason}, semantic search off)"
    return f"{name} ({reason})"


def _verify_provider(provider: Any, reasoning: str) -> None:
    """One tiny generate call, so a bad key fails before the index is built."""
    from repowise.core.providers.llm.base import ProviderError

    with owl_status(console, f"  Verifying provider connection{glyph('ellipsis')}"):
        try:
            run_async(
                provider.generate(
                    "You are a test.",
                    "Reply with OK.",
                    max_tokens=50,
                    reasoning=reasoning,
                )
            )
        except ProviderError as exc:
            from repowise.cli.hints import maybe_hint

            maybe_hint("provider_fail")
            raise reasoned_error(
                f"Provider validation failed: {exc}",
                reason="provider_validation_failed",
            ) from exc
    console.print(f"  [{OK}]{glyph('ok')}[/] Provider connection verified")


def resolve_docs_provider(run: InitRun) -> None:
    """Provider stage of a docs run: resolve, report and verify the model."""
    _resolve_or_fall_back(run)
    # Provider resolution may have just set the API key in os.environ.
    # Re-resolve the embedder so the display and the embed path honor the key
    # the user just pasted, rather than the pre-prompt "mock" fallback.
    run.embedder_name_resolved, run.embedder_reason = resolve_embedder_choice(
        run.opts.embedder_name
    )
    _print_provider_summary(run)
    # Nothing to verify when there is no provider: this run renders from
    # templates and never calls a model.
    if run.provider is not None:
        _verify_provider(run.provider, run.resolved_reasoning)
