"""Opt-in LLM code generation for a single refactoring suggestion.

Resolves the repo's configured provider (BYO key), enriches one selected
suggestion into refactored code + a diff, and renders the result.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from repowise.cli.helpers import console, run_async


def _select_suggestion(suggestions: list, selector: str):
    """Pick one suggestion by a 1-based rank or a target-symbol match.

    ``suggestions`` is the engine's unified-ranked list, so ``"1"`` is the top
    candidate. A non-numeric selector matches ``target_symbol`` exactly first,
    then falls back to a unique case-insensitive substring match.
    """
    if selector.isdigit():
        idx = int(selector) - 1
        if 0 <= idx < len(suggestions):
            return suggestions[idx]
        raise click.ClickException(
            f"Rank {selector} is out of range (1-{len(suggestions)} available)."
        )
    exact = [s for s in suggestions if s.target_symbol == selector]
    if len(exact) == 1:
        return exact[0]
    needle = selector.lower()
    partial = [s for s in suggestions if needle in s.target_symbol.lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise click.ClickException(f"No refactoring suggestion matches {selector!r}.")
    names = ", ".join(sorted({s.target_symbol for s in partial})[:8])
    raise click.ClickException(
        f"{selector!r} matches multiple suggestions ({names}). Use a 1-based rank instead."
    )


def _generate_refactoring_code(repo_path, suggestions: list, selector: str, *, fmt: str) -> None:
    """Opt-in LLM code-gen for one suggestion: resolve provider, enrich, render.

    Reuses the repo's configured provider/model (BYO key). The enrichment layer
    is the only place the refactoring feature touches an LLM; it never runs in
    the indexing hot path.
    """
    from repowise.cli.helpers import resolve_provider
    from repowise.core.analysis.health.refactoring.llm import enrich_suggestion

    if not suggestions:
        raise click.ClickException(
            "No refactoring suggestions for this repo. Run `repowise health "
            "--refactoring-targets` first to see what's available."
        )

    suggestion = _select_suggestion(suggestions, selector)

    try:
        provider = resolve_provider(None, None, Path(repo_path))
    except Exception as exc:  # provider misconfig surfaces as a clean CLI error
        raise click.ClickException(
            f"Could not resolve an LLM provider for code generation: {exc}"
        ) from exc

    console.print(
        f"[dim]Generating code for[/dim] [cyan]{suggestion.target_symbol}[/cyan] "
        f"[dim]({suggestion.refactoring_type}) via {getattr(provider, 'model_name', '?')}...[/dim]"
    )
    result = run_async(enrich_suggestion(suggestion, provider=provider, repo_path=Path(repo_path)))

    if fmt == "json":
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    cached = " [dim](cached)[/dim]" if result.cached else ""
    console.print(
        f"\n[bold]{suggestion.refactoring_type}[/bold] · {suggestion.target_symbol} "
        f"[dim]({result.model})[/dim]{cached}"
    )
    if result.validation.get("status") == "checked":
        v = result.validation
        verdict = (
            "[green]improves cohesion[/green]"
            if v.get("improved")
            else "[yellow]no LCOM4 improvement detected[/yellow]"
        )
        console.print(
            f"[dim]Self-check: LCOM4 {v.get('before_lcom4')} → "
            f"max {v.get('after_max_lcom4')} across {v.get('class_count')} classes — {verdict}[/dim]"
        )
    console.print(result.content)
