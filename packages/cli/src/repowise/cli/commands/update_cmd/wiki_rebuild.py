"""The one-time wiki rebuild a store-format upgrade asks the next ``update`` for.

A release that changes how pages are chosen, grouped or written registers a
``REBUILD_WIKI`` migration. The first ``update`` after the upgrade then runs the
whole generation ladder once, as a generation-config change does: pages the new
build no longer produces retire, new page types appear, and every page is
produced by the new build (model pages whose subject and prompt are unchanged
are reused). Pages built from code cost nothing. Pages a model
writes go through the same cost question as ``init`` and ``generate``: under
the gate, or with ``--yes``, the rebuild runs; over it without a terminal (the
post-commit hook, an agent) the run keeps the current pages and the rebuild
stays due, so nothing is spent that nobody approved.
"""

from __future__ import annotations

from typing import Any

from repowise.cli.helpers import console

from .options import UpdateOptions, UpdateRun


def resolve_wiki_rebuild(opts: UpdateOptions, run: UpdateRun) -> None:
    """Decide whether this run rebuilds the wiki, settling any model spend first."""
    from repowise.core.docs_mode import resolve_docs_mode
    from repowise.core.upgrade import pending_wiki_rebuilds

    owed = pending_wiki_rebuilds(run.state)
    if not owed:
        return
    docs_mode = resolve_docs_mode(run.state)
    # An index-only run on a model wiki cannot write prose; it stays due.
    if docs_mode == "llm" and run.index_only:
        return
    summary = " ".join(m.summary for m in owed)
    if docs_mode != "llm":
        console.print(
            f"[yellow]Upgrading the wiki for this release, built from code at no cost.[/yellow] "
            f"[dim]{summary}[/dim]"
        )
        run.wiki_rebuild = True
        return
    run.wiki_rebuild = _approve_model_spend(opts, run, summary)


def _approve_model_spend(opts: UpdateOptions, run: UpdateRun, summary: str) -> bool:
    """Price rewriting the model-written pages and ask the shared cost question."""
    from repowise.cli.cost_gate import spend_phrase

    est = _estimate_prose_rewrite(opts, run)
    verb = "Would upgrade" if opts.dry_run else "Upgrading"
    console.print(f"[yellow]{verb} the wiki for this release.[/yellow] [dim]{summary}[/dim]")
    if est is not None:
        console.print(f"  Pages written by a model: {spend_phrase(est, opts.concurrency)}.")
    if opts.dry_run:
        return True
    if _spend_blocked(opts, est):
        _report_deferred(est)
        return False
    return True


def _spend_blocked(opts: UpdateOptions, est: Any | None) -> bool:
    """The shared cost question; without a terminal over the gate it defers, never raises."""
    import sys

    from repowise.cli.cost_gate import confirm_cost_gate, cost_gate_blocks

    if opts.yes:
        return False
    try:
        if est is None:
            # No figure to show: ask on a terminal, defer without one.
            return not (sys.stdin.isatty() and confirm_cost_gate("  Rewrite the wiki?"))
        return cost_gate_blocks(
            est,
            yes=False,
            message="  Rewrite the wiki at this cost?",
            defer_without_terminal=True,
        )
    except Exception as exc:  # a failed prompt defers rather than failing the update
        console.print(f"[dim]Cost question unavailable ({exc}).[/dim]")
        return True


def _report_deferred(est: Any | None) -> None:
    from repowise.cli.cost_gate import format_cost

    cost = f" (about {format_cost(est)})" if est is not None else ""
    console.print(
        f"[yellow]The wiki upgrade waits{cost}:[/yellow] this run keeps the current "
        "pages. Run [bold]repowise update --yes[/bold] to rebuild them, or "
        "[bold]repowise update[/bold] in a terminal to be asked."
    )


def _estimate_prose_rewrite(opts: UpdateOptions, run: UpdateRun) -> Any | None:
    """Upper-bound cost of rewriting every model-written page the wiki holds.

    Priced from the stored page ids, before the run parses anything, so a
    deferred rebuild costs the post-commit hook nothing. Pages whose subject
    and prompt are unchanged are reused at generation time, so the real bill
    can be lower; new page types the release adds are not counted. ``None``
    when no estimate can be made.
    """
    try:
        from repowise.cli.helpers import resolve_provider
        from repowise.core.cost_estimator import estimate_cost
        from repowise.core.generation.models import MODEL_WRITTEN_PAGE_TYPES
        from repowise.core.generation.scope import build_cost_plans

        from .deterministic import load_prior_page_ids

        page_ids = load_prior_page_ids(run.repo_path, MODEL_WRITTEN_PAGE_TYPES)
        provider = resolve_provider(opts.provider_name, opts.model, run.repo_path)
        return estimate_cost(
            build_cost_plans(set(page_ids)),
            provider.provider_name,
            provider.model_name,
            repo_path=run.repo_path,
        )
    except Exception:
        return None


def record_wiki_rebuilt(run: UpdateRun, *, clean: bool = True) -> None:
    """Note in the run's state that the rebuild ran, before the final state write.

    *clean* False (a render or persist step failed) leaves the rebuild owed, so
    the next update tries again rather than settling on a partial wiki.
    """
    if run.wiki_rebuild and clean:
        from repowise.core.upgrade import mark_wiki_rebuilt

        mark_wiki_rebuilt(run.state)
