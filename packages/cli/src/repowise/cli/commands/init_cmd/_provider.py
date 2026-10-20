"""Provider stage of ``repowise init``: which model, if any, this run calls."""

from __future__ import annotations

import os
import sys
from typing import Any

import click

from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import console, resolve_explicit_provider_or_prompt, run_async
from repowise.cli.providers import resolve_embedder
from repowise.cli.ui import OK, OWL_SPINNER, VALUE, WARN, print_index_only_intro

from ._options import InitRun

# Keys whose presence lets an index-only run pick a decision-extraction
# provider without a --provider flag.
_DECISION_KEY_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
)


def decision_key_in_env() -> bool:
    """Whether the environment holds a key a decision provider can use."""
    return any(os.environ.get(k) for k in _DECISION_KEY_VARS)


def announce_index_only(run: InitRun) -> None:
    """Tell the user this run builds the wiki from structure."""
    if run.is_interactive:
        print_index_only_intro(console, has_provider=run.decision_provider is not None)
        return
    console.print(f"[bold]repowise index-only[/bold] — {run.repo_path}")
    console.print("Building the wiki from structure [dim]— no model, no spend.[/dim]")
    if run.decision_provider:
        console.print(
            f"Decision extraction provider: [{VALUE}]{run.decision_provider.provider_name}[/]"
        )


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


def note_subscription_cli(run: InitRun) -> None:
    """Name a logged-in Claude Code / Codex / OpenCode on a run no model writes.

    Never switches the run to it: a subscription is spent only when asked for
    with --provider. Interactive runs got the picker instead, and a configured
    provider is already a choice.
    """
    if run.is_interactive or run.opts.provider_name or run.config.get("provider"):
        return
    if not (run.opts.index_only or run.no_provider):
        return
    from repowise.cli.ui.subscription import logged_in_agent_providers, subscription_note

    offered = {n: auth for n, auth in logged_in_agent_providers().items() if auth.offer}
    first = next(iter(offered), None)
    note = subscription_note(tuple(offered), offered[first]) if first else None
    if note:
        console.print(f"  [dim]{note}[/dim]")


def _print_provider_summary(run: InitRun) -> None:
    if not run.is_interactive and not run.no_provider:
        console.print(f"[bold]repowise init[/bold] — {run.repo_path}")
    provider = run.provider
    if provider is not None:
        console.print(
            f"  Provider: [{VALUE}]{provider.provider_name}[/] / Model: [{VALUE}]{provider.model_name}[/]"
        )
    console.print(f"  Embedder: [{VALUE}]{run.embedder_name_resolved}[/]")
    if run.language != "en":
        console.print(f"  Language: [{VALUE}]{run.language}[/]")
    if run.resolved_reasoning != "auto":
        console.print(f"  Reasoning: [{VALUE}]{run.resolved_reasoning}[/]")


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
    run.embedder_name_resolved = resolve_embedder(run.opts.embedder_name)
    _print_provider_summary(run)
    # Nothing to verify when there is no provider: this run renders from
    # templates and never calls a model.
    if run.provider is not None:
        _verify_provider(run.provider, run.resolved_reasoning)
