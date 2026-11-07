"""Multi-repo workspace initialization for ``repowise init``.

When ``init`` is pointed at a directory containing several git repos it branches
here: detect repos, prompt for selection + primary, write the workspace config,
then index (and optionally generate docs for) each repo. Generation routes
through the shared core in :mod:`.generation`; persistence + reporting reuse the
same helpers as the single-repo flow.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from rich.markup import escape

from repowise.cli._setup import setup_logging_silence
from repowise.cli.cost_gate import spend_phrase
from repowise.cli.editor_integrations.defaults import (
    get_default_disabled_project_files,
    get_default_integration_overrides,
    get_default_project_file_overrides,
)
from repowise.cli.editor_setup import (
    register_editor_clients,
    resolve_editor_setup_options,
    write_editor_project_files,
)
from repowise.cli.git_exclude import keep_workspace_data_out
from repowise.cli.helpers import (
    console,
    ensure_repowise_dir,
    get_head_commit,
    load_config,
    load_state,
    resolve_explicit_provider_or_prompt,
    resolve_max_file_pages,
    resolve_provider,
    resolve_reasoning,
    run_async,
    save_config,
    save_config_partial,
    save_state,
)
from repowise.cli.providers import (
    resolve_embedder,
    semantic_search_command,
    semantic_search_status,
)
from repowise.cli.state_persistence import build_kg_state, save_knowledge_graph_json
from repowise.cli.ui import (
    BRAND,
    ERR,
    OK,
    VALUE,
    WARN,
    load_dotenv,
    print_banner,
    progress_steps,
)
from repowise.cli.ui.glyphs import glyph
from repowise.cli.ui.mode_names import pages
from repowise.core.decision_lanes import record_decision_lanes
from repowise.core.docs_mode import docs_mode_state_fields
from repowise.core.generation.styles import DEFAULT_STYLE
from repowise.core.index_scope import MODEL_COMMAND
from repowise.core.workspace.update import stamp_workspace_entry

from ._exit import COST_UNCONFIRMED_BUILT, exit_on_gaps
from ._ingest import structural_generation_config
from ._interactive import (
    offer_distill_rewrite_hook,
    offer_hook_install,
    warn_unresolved_hooks,
)
from ._workspace_config import WorkspacePick, build_workspace_config
from ._workspace_plan import WorkspaceAnswers, WorkspacePlan, run_workspace_plan_screen
from ._workspace_repos import repos_primary_line, select_workspace_repos
from .generation import (
    CostGateDeclined,
    concept_page_count,
    cost_gate_declined,
    embedding_note,
    estimate_generation,
    run_repo_generation,
    structural_page_summary,
)
from .persistence import (
    apply_git_history_coverage_state,
    keep_prior_documentation,
    persist_result,
    stamp_fresh_index,
)
from .reporting import show_workspace_completion


def _run_workspace_generation(
    *,
    repo_path: Path,
    result: Any,
    provider: Any,
    embedder_name_resolved: str,
    concurrency: int,
    yes: bool,
    resume: bool,
    skip_tests: bool,
    skip_infra: bool,
    test_run: bool,
    timings: Any | None = None,
    reasoning: str = "auto",
    onboarding: bool = True,
    wiki_style: str = DEFAULT_STYLE,
    language: str = "en",
    reuse_prior_pages: bool = True,
) -> list[Any]:
    """Run LLM generation for a single repo in the workspace init flow.

    Returns the list of generated pages. Raises :class:`CostGateDeclined` when
    the user declines the cost gate (caller persists the index without docs);
    other errors propagate so the caller can log per-repo failures without
    aborting the whole workspace run.

    ``reuse_prior_pages=False`` is ``--force``: every page is regenerated
    instead of reused from the prior run (issue #1089).
    """
    from repowise.core.generation import GenerationConfig

    gen_config = GenerationConfig.from_repo_config(
        load_config(repo_path),
        max_concurrency=concurrency,
        reasoning=resolve_reasoning(reasoning),
        enable_onboarding=onboarding,
        wiki_style=wiki_style,
        language=language,
        # No question is asked in the workspace flow, so this only picks up a cap
        # already recorded for this repo (by a single-repo init, or by hand).
        max_file_pages=resolve_max_file_pages(config=load_config(repo_path)),
        reuse_prior_pages=reuse_prior_pages,
    )
    plans, est = estimate_generation(
        result=result,
        gen_config=gen_config,
        provider=provider,
        repo_path=repo_path,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        embedder=embedder_name_resolved,
    )

    # Same decision and same stakes as the single-repo flow, so the count and
    # the price carry the same emphasis; in a per-repo loop this line can go by
    # a dozen times, which makes plain text easier to miss, not harder.
    console.print(
        f"    Writing [bold]{concept_page_count(plans):,}[/bold] subsystem pages with "
        f"[{VALUE}]{provider.model_name}[/]. {spend_phrase(est, concurrency)}."
    )
    structural = structural_page_summary(plans)
    if structural:
        console.print(f"    [dim]{structural}[/dim]")
    embedding = embedding_note(est)
    if embedding:
        console.print(f"    [dim]{embedding}[/dim]")

    # Declines rather than raises when it cannot ask (see the note in init's
    # _run_generation_phase): a raise would land in the caller's generic error
    # handler and leave this repo with zero pages, instead of the free template
    # wiki the CostGateDeclined sentinel renders.
    if cost_gate_declined(
        est, yes=yes, message=f"    Continue with {repo_path.name} at this cost?"
    ):
        console.print(
            f"    [{WARN}]Skipped.[/] "
            "[dim]Documentation will be built from code; "
            "future `repowise update` runs stay built from code.[/dim]"
        )
        # Sentinel: caller treats this exactly like an index-only run so the
        # persisted docs mode is not "llm" and the post-commit hook doesn't
        # surprise the user with LLM regen later.
        raise CostGateDeclined()

    return run_repo_generation(
        repo_path=repo_path,
        result=result,
        provider=provider,
        gen_config=gen_config,
        concurrency=concurrency,
        embedder_name_resolved=embedder_name_resolved,
        resume=resume,
        verbose=False,
        test_run=test_run,
        timings=timings,
    )


def _run_workspace_deterministic_generation(
    *,
    repo_path: Path,
    result: Any,
    embedder_name_resolved: str,
    embedder_was_requested: bool,
    concurrency: int,
    resume: bool,
    onboarding: bool,
    wiki_style: str,
    language: str,
    timings: Any | None = None,
) -> tuple[list[Any], str]:
    """Render one workspace repo's wiki from templates (no model, no cost).

    The multi-repo mirror of init's single-repo
    ``_run_deterministic_generation_phase``: an index-only (or provider-less /
    cost-declined) workspace repo gets a complete, upgradable template wiki
    rather than no pages at all, so it reads as a real wiki in the UI. Returns
    the generated pages plus the embedder actually used (persisted by the caller
    so a later workspace update embeds the same way rather than re-deciding).
    """
    # "No key, no spend": a template run must not put a hosted embedder on the
    # bill unless the user named it. ``resolve_embedder`` infers one from any LLM
    # key in the environment, which is the wrong default here. Mirror the
    # single-repo phase — mock unless requested — so full-text search still works
    # and semantic search can be built later with ``repowise reindex``.
    # A previously-persisted embedder on THIS repo counts as requested (re-init
    # case): silently dropping it to the mock would re-embed the store at a
    # different vector width, which the LanceDB writer resolves by dropping the
    # table. The workspace flag is computed once, before any repo config loads,
    # so only the per-repo pin is folded in here — the flag already accounts for
    # the flag and env var, and re-reading them would override a caller that
    # decided otherwise. ``mock`` is not a pin anyone chose, so it does not count.
    from repowise.cli.providers.embedders import pin_names_an_embedder, template_run_embedder
    from repowise.core.generation import GenerationConfig
    from repowise.core.providers.llm.template import TemplateProvider

    repo_config = load_config(repo_path)
    requested = embedder_was_requested or pin_names_an_embedder(repo_config.get("embedder"))
    embedder = template_run_embedder(embedder_name_resolved, requested)

    gen_config = GenerationConfig.from_repo_config(
        repo_config,
        deterministic=True,
        max_concurrency=concurrency,
        language=language,
        enable_onboarding=onboarding,
        wiki_style=wiki_style,
        # No question is asked in the workspace flow, so this only picks up a cap
        # already recorded for this repo (by a single-repo init, or by hand).
        max_file_pages=resolve_max_file_pages(config=repo_config),
    )
    generated_pages = run_repo_generation(
        repo_path=repo_path,
        result=result,
        provider=TemplateProvider(),
        gen_config=gen_config,
        concurrency=concurrency,
        embedder_name_resolved=embedder,
        resume=resume,
        verbose=False,
        timings=timings,
    )
    return generated_pages, embedder


def _workspace_generation_provider_for_repo(provider: Any, repo_path: Path) -> Any:
    """Return a generation provider bound to the current workspace repo.

    A provider that runs its CLI in the repo's directory (``needs_repo_cwd``)
    is re-resolved against each repo's path; all other providers are
    path-agnostic and returned unchanged.
    """
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    name = getattr(provider, "provider_name", None)
    spec = PROVIDER_SPECS.get(name) if isinstance(name, str) else None
    if spec is None or not spec.needs_repo_cwd:
        return provider
    return resolve_provider(name, getattr(provider, "model_name", None), repo_path)


@dataclass
class _WorkspaceCtx:
    """Run-wide settings shared across per-repo indexing in a workspace init."""

    provider: Any
    ws_config: Any
    editor_options: Any
    index_only: bool
    dry_run: bool
    force: bool
    follow_renames: bool
    include_submodules: bool
    exclude_patterns: list[str]
    skip_tests: bool
    skip_infra: bool
    concurrency: int
    test_run: bool
    yes: bool
    resume: bool
    onboarding: bool
    wiki_style: str
    language: str
    resolved_reasoning: str
    embedder_name_resolved: str
    embedder_was_requested: bool
    resolved_commit_limit: int
    run_mode: str = "standard"
    #: No workspace config existed before this run, so the MCP entry is new.
    first_index: bool = True
    #: Mirrors the single-repo flag. A workspace writes project files per repo,
    #: so --no-editor-setup has to reach every one of them, not just the
    #: primary's client registration.
    editor_setup: bool = True
    #: Same reasoning for key persistence: each workspace repo gets its own
    #: .repowise/, so each needs its own credential to be MCP-answerable.
    save_key: bool = True
    #: Asked-for work a repo did not get; any entry makes init exit degraded.
    gaps: list[str] = field(default_factory=list)


@dataclass
class _RepoOutcome:
    """Result of indexing a single repo within a workspace."""

    error: str | None = None
    file_count: int = 0
    symbol_count: int = 0
    pages_generated: int = 0
    docs_outcome: tuple[int, str | None] = (0, None)
    #: Pages a real embedder failed to write; the repo is still persisted.
    embed_failed_pages: int = 0


@dataclass
class _RepoIndex:
    """One workspace repo's pipeline result, timing recorder and bound provider."""

    result: Any
    callback: Any
    provider: Any


@dataclass
class _RepoDocs:
    """What generation did for one workspace repo.

    ``docs_mode`` is "llm" when a model wrote the pages, "deterministic" when
    they were rendered from templates (index-only, no provider, or a declined
    cost gate), "none" when nothing was generated (dry run, fast mode, or a
    generation error). It drives the persisted state so `update` and the web
    UI see the truth.
    """

    docs_mode: str = "none"
    skip_reason: str | None = None
    pages_generated: int = 0
    det_embedder: str | None = None


def _ingest_and_generate_repo(repo: Any, idx: int, total: int, ctx: _WorkspaceCtx) -> _RepoOutcome:
    """Index (and optionally generate docs for) one repo in a workspace init.

    On pipeline failure the outcome carries ``error`` and nothing is persisted.
    """
    console.print(
        f"  [{BRAND}][{idx}/{total}][/] Indexing [bold]{repo.alias}[/bold] ({repo.path.name}){glyph('ellipsis')}"
    )
    ensure_repowise_dir(repo.path)

    # The index phase mines decisions with a model too, so it needs the provider,
    # bound to this repo: a provider that shells out runs in the repo's directory.
    repo_provider = (
        None if ctx.dry_run else _workspace_generation_provider_for_repo(ctx.provider, repo.path)
    )
    try:
        index = _index_repo(repo, ctx, repo_provider)
    except Exception as exc:
        console.print(f"    [{ERR}]{glyph('fail')} Failed: {exc}[/]\n")
        return _RepoOutcome(error=str(exc))
    result = index.result

    docs = _generate_repo_docs(repo, ctx, index)
    docs_outcome = (len(result.generated_pages or []), docs.skip_reason)

    # A dry run writes nothing for this repo: the index was computed in memory
    # only so the plan and per-repo counts can be shown.
    if ctx.dry_run:
        return _RepoOutcome(
            file_count=result.file_count,
            symbol_count=result.symbol_count,
            pages_generated=docs.pages_generated,
            docs_outcome=docs_outcome,
        )

    persist_warnings = _persist_repo_index(repo.path, index)
    head = get_head_commit(repo.path)
    state = _repo_state(head, repo.path, ctx, index, docs, persist_warnings)
    # Fully indexed here (concept tree included), so stamp the terminal store
    # format rather than clamping below the reindex gate.
    save_state(repo.path, state, full_index=True)

    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        save_knowledge_graph_json(repo.path, kg)

    entry = ctx.ws_config.get_repo(repo.alias)
    if entry is not None:
        stamp_workspace_entry(entry, repo.path, head)

    write_editor_project_files(
        console,
        repo.path,
        options=ctx.editor_options,
        no_editor_setup=not ctx.editor_setup,
    )
    _save_repo_config(repo.path, ctx, docs)
    # After the config writes, which the config fingerprint reads. Unstamped,
    # the first update re-renders and re-scores every repo init just built.
    stamp_fresh_index(state, repo.path, result)
    save_state(repo.path, state, full_index=True)

    from repowise.cli.providers import embed_failure_message

    failed = getattr(result, "embed_failed_pages", 0)
    return _RepoOutcome(
        file_count=result.file_count,
        symbol_count=result.symbol_count,
        pages_generated=docs.pages_generated,
        docs_outcome=docs_outcome,
        embed_failed_pages=(
            failed if embed_failure_message(_scope_embedder(ctx, docs), failed) else 0
        ),
    )


def _index_repo(repo: Any, ctx: _WorkspaceCtx, repo_provider: Any) -> _RepoIndex:
    """Run the index pipeline (no docs) for one repo under a progress bar."""
    from repowise.core.pipeline import PhaseTimingRecorder, run_pipeline
    from repowise.core.pipeline.modes import OrchestratorMode

    with progress_steps(console) as steps:
        callback = PhaseTimingRecorder(steps)

        _prev_state = load_state(repo.path)
        _prev_kg_fp = (
            _prev_state.get("knowledge_graph", {}).get("fingerprint") if not ctx.force else None
        )

        result = run_async(
            run_pipeline(
                repo.path,
                commit_depth=ctx.resolved_commit_limit,
                follow_renames=ctx.follow_renames,
                exclude_patterns=ctx.exclude_patterns if ctx.exclude_patterns else None,
                include_submodules=ctx.include_submodules,
                generate_docs=False,
                llm_client=repo_provider,
                mode=(
                    OrchestratorMode.FAST
                    if ctx.run_mode == "fast"
                    else OrchestratorMode.STANDARD
                ),
                progress=callback,
                existing_kg_fingerprint=_prev_kg_fp,
                derive_environment_facts=True,
                generation_config=structural_generation_config(ctx.index_only),
            )
        )
    console.print(f"    [{OK}]{glyph('ok')}[/] {result.file_count:,} files, {result.symbol_count:,} symbols")
    return _RepoIndex(result=result, callback=callback, provider=repo_provider)


def _generate_repo_docs(repo: Any, ctx: _WorkspaceCtx, index: _RepoIndex) -> _RepoDocs:
    """The per-repo generation phase."""
    docs = _RepoDocs()
    if ctx.dry_run:
        console.print(
            f"    [{WARN}]Dry run: `.repowise/` created; "
            "no DB, state, or pages written for this repo.[/]\n"
        )
        docs.skip_reason = "dry run"
    elif ctx.run_mode == "fast":
        # Fast mode is a graph-and-git index by design, as in single-repo init;
        # the panel names it Index only rather than calling it skipped.
        console.print()
    elif not ctx.index_only and ctx.provider is not None:
        _generate_repo_with_model(repo, ctx, index, docs)
    else:
        # Index-only, or provider setup fell back to index-only: a complete
        # template wiki rather than an empty index with no upgrade target.
        _render_repo_from_templates(repo, ctx, index, docs)
    return docs


def _generate_repo_with_model(
    repo: Any, ctx: _WorkspaceCtx, index: _RepoIndex, docs: _RepoDocs
) -> None:
    """Write the repo's wiki with the model; templates when the cost is declined."""
    result = index.result
    try:
        generated_pages = _run_workspace_generation(
            repo_path=repo.path,
            result=result,
            provider=index.provider,
            embedder_name_resolved=ctx.embedder_name_resolved,
            timings=index.callback.table,
            concurrency=ctx.concurrency,
            yes=ctx.yes,
            resume=ctx.resume,
            skip_tests=ctx.skip_tests,
            skip_infra=ctx.skip_infra,
            test_run=ctx.test_run,
            reasoning=ctx.resolved_reasoning,
            onboarding=ctx.onboarding,
            wiki_style=ctx.wiki_style,
            language=ctx.language,
            reuse_prior_pages=not ctx.force,
        )
        result.generated_pages = generated_pages
        # ``result.vector_store`` is set inside the generation call.
        # Placeholders left by failed provider calls are not counted, as in
        # single-repo init.
        from repowise.core.generation.models import count_stub_fallbacks

        docs.pages_generated = len(generated_pages) - count_stub_fallbacks(generated_pages)
        docs.docs_mode = "llm"
        console.print(f"    [{OK}]{glyph('ok')}[/] Wrote {pages(docs.pages_generated)} with the model\n")
    except CostGateDeclined:
        # Declining only meant "not at that price": fall back to the free
        # template renderer rather than leave the repo with no wiki.
        if not sys.stdin.isatty():
            ctx.gaps.append(f"{repo.alias}: {COST_UNCONFIRMED_BUILT}")
        console.print(
            "    [dim]Building this repo's documentation from code instead. Run "
            f"[bold]{MODEL_COMMAND} {escape(str(repo.path))}[/bold] to write it with a model "
            "later.[/dim]"
        )
        _render_repo_from_templates(repo, ctx, index, docs)
    except Exception as gen_exc:
        console.print(f"    [{ERR}]{glyph('fail')} Generation failed: {gen_exc}[/]\n")
        docs.skip_reason = f"generation error: {gen_exc}"
        ctx.gaps.append(f"{repo.alias}: documentation not written ({gen_exc})")
        result.generated_pages = []


def _render_repo_from_templates(
    repo: Any, ctx: _WorkspaceCtx, index: _RepoIndex, docs: _RepoDocs
) -> None:
    """Render the whole wiki from templates and mark this repo deterministic."""
    generated_pages, docs.det_embedder = _run_workspace_deterministic_generation(
        repo_path=repo.path,
        result=index.result,
        embedder_name_resolved=ctx.embedder_name_resolved,
        embedder_was_requested=ctx.embedder_was_requested,
        timings=index.callback.table,
        concurrency=ctx.concurrency,
        resume=ctx.resume,
        onboarding=ctx.onboarding,
        wiki_style=ctx.wiki_style,
        language=ctx.language,
    )
    index.result.generated_pages = generated_pages
    docs.pages_generated = len(generated_pages)
    docs.docs_mode = "deterministic"
    console.print(
        f"    [{OK}]{glyph('ok')}[/] Built {pages(len(generated_pages))} from code (no model)\n"
    )


def _persist_repo_index(repo_path: Path, index: _RepoIndex) -> list[str]:
    """Persist to the repo-local DB; returns the persist phase's warnings."""
    persist_warnings: list[str] = []
    callback = index.callback
    with progress_steps(console, transient=True) as persist_steps:
        persist_callback = callback.rebind(persist_steps)
        persist_callback.on_phase_start("persist", None)
        try:
            run_async(persist_result(index.result, repo_path, persist_callback, callback.table))
        finally:
            try:
                persist_callback.on_phase_done("persist")
                persist_warnings.extend(persist_callback.warnings)
            except RuntimeError as e:
                if "Event loop is closed" not in str(e):
                    raise
    return persist_warnings


def _scope_embedder(ctx: _WorkspaceCtx, docs: _RepoDocs) -> str | None:
    """The embedder this repo's pages were embedded with, if any."""
    if docs.docs_mode == "deterministic":
        return docs.det_embedder
    return ctx.embedder_name_resolved if docs.docs_mode == "llm" else None


def _repo_state(
    head: str | None,
    repo_path: Path,
    ctx: _WorkspaceCtx,
    index: _RepoIndex,
    docs: _RepoDocs,
    persist_warnings: list[str],
) -> dict[str, Any]:
    """state.json for one repo, so `repowise update` knows the base commit."""
    result = index.result
    pages_count = len(result.generated_pages or [])
    state: dict[str, Any] = {
        "last_sync_commit": head,
        "total_pages": pages_count,
        **docs_mode_state_fields(docs.docs_mode),
    }
    if docs.docs_mode == "llm" and ctx.provider is not None:
        state["provider"] = ctx.provider.provider_name
        state["model"] = ctx.provider.model_name
    # Read after generation and persistence: both write into the same table.
    repo_phase_timings: dict[str, float] = index.callback.timings
    if repo_phase_timings:
        state["phase_timings"] = repo_phase_timings
    apply_git_history_coverage_state(state, result)
    _stamp_repo_scope(state, repo_path, ctx, index, docs)
    keep_prior_documentation(state, load_state(repo_path), ctx.run_mode)
    kg = getattr(result, "knowledge_graph_result", None)
    if kg is not None:
        state["knowledge_graph"] = build_kg_state(kg)
    state.pop("degraded", None)
    if persist_warnings:
        state["degraded"] = persist_warnings
    return state


def _stamp_repo_scope(
    state: dict[str, Any], repo_path: Path, ctx: _WorkspaceCtx, index: _RepoIndex, docs: _RepoDocs
) -> None:
    """Stamp what this index covers: tiers, file pages, provider and search."""
    from repowise.core.index_scope import dropped_files_scope, stamp_index_scope

    result, repo_provider, docs_mode = index.result, index.provider, docs.docs_mode
    # A model can write the docs without mining decisions (workspace add).
    scope_provider = ctx.provider if repo_provider is None and docs_mode == "llm" else repo_provider
    scope_config = load_config(repo_path)
    configured_cap = resolve_max_file_pages(config=scope_config)
    scope_embedder = _scope_embedder(ctx, docs)
    semantic = (
        semantic_search_status(scope_embedder, getattr(result, "embed_failed_pages", 0))
        if result.generated_pages
        else "unavailable"
    )
    state["run_mode"] = ctx.run_mode
    state["git_tier"] = "essential" if ctx.run_mode == "fast" else "full"
    record_decision_lanes(state, repo_path, ran_with_model=repo_provider is not None)
    unavailable = [] if getattr(result, "health_report", None) is not None else ["health"]
    stamp_index_scope(
        state,
        {**scope_config, "commit_limit": ctx.resolved_commit_limit},
        run_mode=ctx.run_mode,
        content_provenance={"none": "none", "deterministic": "template", "llm": "model"}[docs_mode],
        git_tier=state["git_tier"],
        git_commit_cap=ctx.resolved_commit_limit,
        dropped_files=dropped_files_scope(getattr(result, "traversal_stats", None)),
        test_run=ctx.test_run and docs_mode == "llm",
        file_pages={"configured_cap": configured_cap, **_file_page_fields(result, configured_cap)},
        analysis={
            "unavailable": unavailable,
            "skipped": ["generation"] if ctx.run_mode == "fast" else [],
        },
        provider={
            "name": scope_provider.provider_name if scope_provider is not None else None,
            "model": scope_provider.model_name if scope_provider is not None else None,
            "embedder": scope_embedder,
            "reused": False,
            "model_cost_possible": docs_mode == "llm",
        },
        search={
            "full_text": "available" if result.generated_pages else "unavailable",
            "semantic": semantic,
            "next_command": (
                semantic_search_command(repo_path, scope_embedder)
                if result.generated_pages and semantic == "unavailable"
                else None
            ),
        },
        upgrade={
            "status": "pending" if docs_mode != "llm" else "not_applicable",
            "retryable": docs_mode != "llm",
            "completed_stages": [],
        },
    )


def _file_page_fields(result: Any, configured_cap: int | None) -> dict[str, Any]:
    """File-page scope: the generator's own, else counted from the parsed files."""
    from repowise.core.generation.selection import count_documentable_files
    from repowise.core.index_scope import file_page_scope

    if getattr(result, "generation_scope", None):
        return result.generation_scope
    if hasattr(result, "parsed_files"):
        return file_page_scope(
            configured_cap=configured_cap,
            eligible=count_documentable_files(result.parsed_files),
            generated_pages=result.generated_pages,
        )
    return {"effective_cap": None, "eligible": None, "generated": None, "omitted": None}


def _save_repo_config(repo_path: Path, ctx: _WorkspaceCtx, docs: _RepoDocs) -> None:
    """Persist the per-repo config the run's docs mode calls for."""
    provider = ctx.provider
    if docs.docs_mode == "llm" and provider is not None:
        from repowise.cli.providers.embedders import resolve_embedding_model

        save_config(
            repo_path,
            provider.provider_name,
            provider.model_name,
            ctx.embedder_name_resolved,
            embedding_model=resolve_embedding_model(ctx.embedder_name_resolved),
            exclude_patterns=ctx.exclude_patterns if ctx.exclude_patterns else None,
            commit_limit=ctx.resolved_commit_limit,
            reasoning=ctx.resolved_reasoning,
            save_key=ctx.save_key,
        )
        # Only a non-default style or language is recorded, so update and
        # restyle honor it.
        if ctx.wiki_style != DEFAULT_STYLE:
            save_config_partial(repo_path, wiki_style=ctx.wiki_style)
        if ctx.language != "en":
            save_config_partial(repo_path, language=ctx.language)
    elif docs.docs_mode == "deterministic":
        from repowise.cli.providers.embedders import resolve_embedding_model

        # A template wiki still embeds pages: persist the embedder actually used,
        # or a later update could re-embed the store at a different width.
        # Provider, model, style and language only reach a model.
        det_embedder = docs.det_embedder
        save_config_partial(
            repo_path,
            exclude_patterns=ctx.exclude_patterns if ctx.exclude_patterns else None,
            commit_limit=ctx.resolved_commit_limit if ctx.resolved_commit_limit else None,
            embedder=det_embedder,
            embedding_model=(resolve_embedding_model(det_embedder) if det_embedder else None),
        )


def _run_cross_repo_analysis(ws_config: Any, root: Any, selected: list[Any], errors: list) -> None:
    """Run cross-repo analysis (co-changes, package deps, contracts) when ≥2 repos indexed."""
    indexed_aliases = [repo.alias for repo in selected if repo.alias not in [e[0] for e in errors]]
    if len(indexed_aliases) >= 2:
        console.print("  Running cross-repo analysis...")
        try:
            from repowise.core.workspace.update import run_cross_repo_hooks

            run_async(run_cross_repo_hooks(ws_config, root, indexed_aliases))
            console.print(f"  [{OK}]{glyph('ok')}[/] Cross-repo analysis complete")
        except Exception as exc:
            console.print(f"  [{WARN}]{glyph('warn')} Cross-repo analysis failed: {exc}[/]")


@dataclass
class _WorkspaceTotals:
    """What the per-repo loop indexed, and what failed."""

    total_files: int = 0
    total_symbols: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)
    #: alias -> (generated_count, skip_reason | None), shown in the completion
    #: panel so the user never has to guess why a repo has no pages.
    docs_outcomes: dict[str, tuple[int, str | None]] = field(default_factory=dict)
    embed_failures: dict[str, int] = field(default_factory=dict)


def _workspace_init(
    *,
    scan: Any,
    init_all: bool,
    repo_names: list[str],
    exclude_patterns: list[str],
    commit_limit: int | None,
    follow_renames: bool,
    no_claude_md: bool,
    agents_md: bool | None,
    codex_setup: bool | None,
    distill_hook: bool | None,
    hook: bool | None,
    editor_setup: bool,
    save_key: bool,
    include_submodules: bool,
    # Generation params (passed through from init_command)
    provider_name: str | None = None,
    model: str | None = None,
    embedder_name: str | None = None,
    index_only: bool = False,
    skip_tests: bool = False,
    skip_infra: bool = False,
    concurrency: int = 10,
    test_run: bool = False,
    reasoning: str | None = None,
    yes: bool = False,
    dry_run: bool = False,
    resume: bool = False,
    force: bool = False,
    onboarding: bool = True,
    wiki_style: str = DEFAULT_STYLE,
    language: str | None = None,
    run_mode: str = "standard",
) -> None:
    """Multi-repo workspace initialization.

    Detects repos, prompts for selection and primary, creates a workspace
    config, then runs ingestion on each repo.  When the user selects full or
    advanced mode (interactively) or passes an explicit provider, also runs
    LLM generation per repo.
    """
    setup_logging_silence()

    start = time.monotonic()
    root = scan.root

    print_banner(console, repo_name=root.name, workspace=True)

    answers = WorkspaceAnswers(
        index_only=index_only,
        provider_name=provider_name,
        model=model,
        reasoning=reasoning,
        commit_limit=commit_limit,
        follow_renames=follow_renames,
        skip_tests=skip_tests,
        skip_infra=skip_infra,
        concurrency=concurrency,
        exclude_patterns=exclude_patterns,
        test_run=test_run,
        onboarding=onboarding,
        wiki_style=wiki_style,
        language=language,
        embedder_name=embedder_name,
    )
    editor_options = _workspace_editor_options(no_claude_md, agents_md, codex_setup)
    picked = _settle_repos(
        scan,
        answers,
        repo_names=repo_names,
        interactive=sys.stdin.isatty() and provider_name is None and not index_only and not yes,
        select_all=init_all or yes or not sys.stdin.isatty(),
        editor_options=editor_options,
        editor_setup=editor_setup,
        save_key=save_key,
        hook=hook,
    )
    if picked is None:
        return
    pick, plan = picked
    selected, primary_alias = pick.selected, pick.primary_alias
    if plan is not None:
        answers = plan.answers
        hook = plan.sync if plan.sync is not None else hook
    primary_repo = pick.primary_repo
    load_dotenv(primary_repo.path)
    answers.embedder_name_resolved = resolve_embedder(answers.embedder_name)
    # Whether the user named this embedder, rather than resolve_embedder
    # inferring one from an LLM key: the free template path will not put a
    # hosted embedder on the user's bill unless they named it.
    embedder_was_requested = bool(answers.embedder_name) or bool(
        os.environ.get("REPOWISE_EMBEDDER", "").strip()
    )
    provider, resolved_reasoning = _resolve_workspace_provider(
        answers, primary_repo, yes=yes, save_key=save_key
    )
    from repowise.core.workspace.config import WORKSPACE_CONFIG_FILENAME

    first_index = not (root / WORKSPACE_CONFIG_FILENAME).exists()
    ws_config = _workspace_config(pick, ask=sys.stdin.isatty() and not yes, dry_run=dry_run)
    if repo_names:
        console.print(repos_primary_line(ws_config.default_repo, pick))

    from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT

    ctx = _WorkspaceCtx(
        provider=provider,
        ws_config=ws_config,
        editor_options=editor_options,
        editor_setup=editor_setup,
        save_key=save_key,
        index_only=answers.index_only,
        dry_run=dry_run,
        force=force,
        follow_renames=answers.follow_renames,
        include_submodules=include_submodules,
        exclude_patterns=answers.exclude_patterns,
        skip_tests=answers.skip_tests,
        skip_infra=answers.skip_infra,
        concurrency=answers.concurrency,
        test_run=answers.test_run,
        yes=yes,
        resume=resume,
        onboarding=answers.onboarding,
        wiki_style=answers.wiki_style,
        language=answers.language or "en",
        resolved_reasoning=resolved_reasoning,
        embedder_name_resolved=answers.embedder_name_resolved,
        embedder_was_requested=embedder_was_requested,
        resolved_commit_limit=max(1, min(answers.commit_limit or _DEFAULT_COMMIT_LIMIT, 10000)),
        run_mode=run_mode,
        first_index=first_index,
    )
    totals = _index_workspace_repos(selected, ctx)

    # A dry run writes nothing for any repo, so nothing at the workspace level.
    if not dry_run:
        _save_workspace(
            ws_config,
            root,
            selected,
            totals.errors,
            editor_setup=editor_setup,
            yes=yes,
            global_consent=plan.global_consent if plan is not None else None,
        )

    if not dry_run:
        _offer_workspace_hooks(
            selected,
            totals.errors,
            hook=hook,
            distill_hook=distill_hook,
            yes=yes,
            editor_setup=editor_setup,
        )

    # Last, so Connected and Auto-sync read what the hook offers left behind.
    show_workspace_completion(
        selected=selected,
        errors=totals.errors,
        total_files=totals.total_files,
        total_symbols=totals.total_symbols,
        primary_alias=primary_alias,
        elapsed=time.monotonic() - start,
        index_only=answers.index_only,
        provider=provider,
        docs_outcomes=totals.docs_outcomes,
        dry_run=dry_run,
        setup=_workspace_setup(primary_repo.path, ctx),
    )
    console.print()
    exit_on_gaps(_workspace_gaps(ctx, totals))


def _workspace_setup(primary_path: Path, ctx: _WorkspaceCtx) -> Any:
    """What editor setup left for the primary repo; None on a dry run, which set nothing up."""
    if ctx.dry_run:
        return None
    from repowise.cli.editor_setup import detect_editor_setup_outcome

    return detect_editor_setup_outcome(
        primary_path,
        interactive=sys.stdin.isatty() and not ctx.yes,
        first_index=ctx.first_index,
        no_editor_setup=not ctx.editor_setup,
    )


def _settle_repos(
    scan: Any,
    answers: WorkspaceAnswers,
    *,
    repo_names: list[str],
    interactive: bool,
    select_all: bool,
    editor_options: Any,
    editor_setup: bool,
    save_key: bool,
    hook: bool | None,
) -> tuple[WorkspacePick, WorkspacePlan | None] | None:
    """The picked repos and primary, and the plan screen's answers; None to stop.

    ``--repos`` is matched first (an unknown name stops the run); the plan
    screen then shows only those repos. *interactive* shows the plan screen (a
    terminal, no --yes, --provider or --no-prose); *select_all* takes every
    repo without asking.
    """
    named = None
    if repo_names:
        named = select_workspace_repos(scan, repo_names=repo_names, select_all=True)
        if named is None:
            return None
        scan = replace(scan, repos=list(named.selected))
    if interactive:
        plan = run_workspace_plan_screen(
            scan,
            answers,
            editor_options=editor_options,
            editor_setup=editor_setup,
            save_key=save_key,
            hook=hook,
        )
        if plan is not None:
            primary_repo = next(r for r in plan.selected if r.alias == plan.primary_alias)
            # The screen defaults to the first repo; only a changed primary replaces
            # the one an existing workspace config names.
            primary_picked = plan.primary_alias != plan.selected[0].alias
            pick = WorkspacePick(
                plan.selected, scan.root, plan.primary_alias, primary_repo, primary_picked
            )
            return pick, plan
        # Stdin ended, so nobody is there to pick repos either.
        select_all = True
    if named is not None:
        return named, None
    pick = select_workspace_repos(scan, repo_names=[], select_all=select_all)
    return None if pick is None else (pick, None)


def _save_workspace(
    ws_config: Any,
    root: Path,
    selected: list[Any],
    errors: list,
    *,
    editor_setup: bool,
    yes: bool,
    global_consent: bool | None = None,
) -> None:
    """Save the indexed timestamps, run cross-repo analysis, register the primary.

    *global_consent* is the plan screen's answer for the global MCP entry;
    ``None`` when the screen did not ask.
    """
    ws_config.save(root)
    _run_cross_repo_analysis(ws_config, root, selected, errors)
    primary_entry = ws_config.get_primary()
    if not primary_entry:
        return
    from repowise.cli.editor_integrations.global_mcp import (
        REGISTER_LATER_COMMAND,
        confirm_global_mcp,
    )

    primary_path = (root / primary_entry.path).resolve()
    if global_consent is False:
        console.print(
            "  [dim]Global Claude config left as is (this workspace only). Run "
            f"'{REGISTER_LATER_COMMAND}' in the primary repo to point it here later.[/dim]"
        )
    elif confirm_global_mcp(
        console,
        primary_path,
        # Answered on the plan screen: print old and new, do not ask again.
        yes=yes or global_consent is True,
        no_editor_setup=not editor_setup,
    ):
        register_editor_clients(console, primary_path)
        # The repos' .mcp.json files were written before the user-scope entry
        # existed; drop each one that entry now serves, as single-repo init does.
        from repowise.cli.agent_targets.targets.claude_code import sync_project_mcp_config

        for repo in selected:
            # Best effort: a malformed .mcp.json was already reported when written.
            with contextlib.suppress(Exception):
                sync_project_mcp_config(Path(repo.path))


def _resolve_workspace_provider(
    answers: WorkspaceAnswers,
    primary_repo: Any,
    *,
    yes: bool,
    save_key: bool,
) -> tuple[Any, str]:
    """Resolve the provider once for every repo; index-only when setup fails."""
    primary_cfg = load_config(primary_repo.path)
    resolved_reasoning = resolve_reasoning(answers.reasoning, primary_cfg)
    provider = None
    if not answers.index_only:
        try:
            provider = resolve_explicit_provider_or_prompt(
                answers.provider_name,
                answers.model,
                primary_repo.path,
                interactive=sys.stdin.isatty() and not yes and not answers.index_only,
                save_key=save_key,
            )
            # Re-resolve: interactive provider selection may just have set the
            # provider's API key in os.environ.
            answers.embedder_name_resolved = resolve_embedder(answers.embedder_name)
            console.print(
                f"  Provider: [{VALUE}]{provider.provider_name}[/] / "
                f"Model: [{VALUE}]{provider.model_name}[/]"
            )
            console.print(f"  Embedder: [{VALUE}]{answers.embedder_name_resolved}[/]\n")
            if resolved_reasoning != "auto":
                console.print(f"  Reasoning: [{VALUE}]{resolved_reasoning}[/]\n")
        except Exception as exc:
            if answers.provider_name is not None:
                raise
            console.print(
                f"  [{WARN}]Provider setup failed ({exc}); building documentation from code.[/]"
            )
            answers.index_only = True
            provider = None
    return provider, resolved_reasoning


def _workspace_config(pick: WorkspacePick, *, ask: bool, dry_run: bool) -> Any:
    """The workspace config for the selected repos, saved unless this is a dry run."""
    from repowise.core.workspace import WORKSPACE_CONFIG_FILENAME

    root = pick.root
    verb = "Updated" if (root / WORKSPACE_CONFIG_FILENAME).is_file() else "Created"
    ws_config = build_workspace_config(pick, console=console, ask=ask)
    if dry_run:
        # `.repowise/` is still created per repo, so a later distill-hook
        # decline can gate every selected path; nothing is written into it.
        console.print(
            f"  [{WARN}]Dry run: `.repowise/` directories are created, "
            "but no config, index, or pages are written.[/]"
        )
    else:
        config_path = ws_config.save(root)
        console.print(f"  [{OK}]{glyph('ok')}[/] {verb} {config_path.name}")
        if excluded := keep_workspace_data_out(root):
            console.print(f"  [dim]Kept out of git (.git/info/exclude): {', '.join(excluded)}[/dim]")
    console.print()
    return ws_config


def _workspace_editor_options(
    no_claude_md: bool, agents_md: bool | None, codex_setup: bool | None
) -> Any:
    """Editor files from the defaults and flags.

    No agent checklist here: the question is per repo, so asking once would
    apply one answer everywhere. `repowise agents add` covers the rest.
    """
    return resolve_editor_setup_options(
        disabled_project_files=get_default_disabled_project_files(
            no_claude_md=no_claude_md,
        ),
        project_file_overrides=get_default_project_file_overrides(
            agents_md=agents_md,
        ),
        integration_overrides=get_default_integration_overrides(
            codex_setup=codex_setup,
        ),
    )


def _index_workspace_repos(selected: list[Any], ctx: _WorkspaceCtx) -> _WorkspaceTotals:
    """Index (always generate_docs=False; generation is separate) each selected repo."""
    totals = _WorkspaceTotals()
    for i, repo in enumerate(selected, 1):
        outcome = _ingest_and_generate_repo(repo, i, len(selected), ctx)
        if outcome.error:
            totals.errors.append((repo.alias, outcome.error))
            continue
        totals.total_files += outcome.file_count
        totals.total_symbols += outcome.symbol_count
        totals.docs_outcomes[repo.alias] = outcome.docs_outcome
        if outcome.embed_failed_pages:
            totals.embed_failures[repo.alias] = outcome.embed_failed_pages
    return totals


def _offer_workspace_hooks(
    selected: list[Any],
    errors: list[tuple[str, str]],
    *,
    hook: bool | None,
    distill_hook: bool | None,
    yes: bool,
    editor_setup: bool,
) -> None:
    """Post-commit hooks for the indexed repos, then the distill rewrite hook."""
    indexed_repos = [repo for repo in selected if repo.alias not in [e[0] for e in errors]]
    if indexed_repos:
        offer_hook_install(
            console,
            [r.path for r in indexed_repos],
            aliases=[r.alias for r in indexed_repos],
            flag=hook,
            yes=yes,
            no_editor_setup=not editor_setup,
        )
    # Every selected repo, even a failed one: each already has `.repowise/`, and
    # the hook treats a `.repowise/` with no recorded verdict as enabled.
    offer_distill_rewrite_hook(
        console,
        [r.path for r in selected],
        distill_hook,
        yes=yes,
        no_editor_setup=not editor_setup,
    )
    warn_unresolved_hooks(
        console, [r.path for r in selected], no_editor_setup=not editor_setup
    )


def _workspace_gaps(ctx: _WorkspaceCtx, totals: _WorkspaceTotals) -> list[str]:
    """Every repo's gaps, raised after each one is persisted, as single-repo init does."""
    gaps = list(ctx.gaps)
    if totals.errors:
        failed = ", ".join(alias for alias, _ in totals.errors)
        gaps.append(f"{len(totals.errors):,} repo(s) failed to index ({failed})")
    if totals.embed_failures:
        listed = ", ".join(f"{alias} ({n:,} page(s))" for alias, n in totals.embed_failures.items())
        gaps.append(
            f"Embedding failed for {listed}, so semantic search is unavailable there "
            "(full-text search still works). Fix the cause in the warnings above, "
            "then run: repowise reindex in each"
        )
    return gaps
