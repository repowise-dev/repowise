"""Provider stage of ``repowise init``: which model, if any, this run calls."""

from __future__ import annotations

import sys
from typing import Any

import click

from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import console, resolve_explicit_provider_or_prompt, run_async
from repowise.cli.providers import resolve_embedder_choice, template_run_embedder
from repowise.cli.ui import OK, OWL_SPINNER, VALUE, WARN, print_index_only_intro

from ._options import InitRun


def announce_index_only(run: InitRun) -> None:
    """Tell the user this run builds the wiki from structure."""
    if run.is_interactive:
        print_index_only_intro(console)
        return
    console.print(f"[bold]repowise index-only[/bold] — {run.repo_path}")
    console.print("Building the wiki from structure [dim]— no model, no spend.[/dim]")


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
            console.print(f"[bold]repowise init[/bold] — {run.repo_path}")
        console.print(
            f"[{WARN}]No model configured.[/] Building the wiki from "
            "structure instead — no key, no spend.\n"
            "[dim]Set a key (or pass --provider) and run [bold]repowise "
            "update --full[/bold] to have a model write it.[/dim]"
        )
        # A provider that was configured but could not be built is a different
        # problem from none being configured, and the message above would send
        # the user to set a key they have already set.
        if "Could not set up" in str(exc):
            console.print(f"  [dim]{exc}[/dim]")


def _print_provider_summary(run: InitRun) -> None:
    if not run.is_interactive and not run.no_provider:
        console.print(f"[bold]repowise init[/bold] — {run.repo_path}")
    provider = run.provider
    if provider is not None:
        console.print(
            f"  Provider: [{VALUE}]{provider.provider_name}[/] / Model: [{VALUE}]{provider.model_name}[/]"
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

    with console.status("  Verifying provider connection…", spinner=OWL_SPINNER):
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
    console.print(f"  [{OK}]✓[/] Provider connection verified")


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
