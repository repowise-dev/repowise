"""``repowise doctor`` — health check for the wiki setup."""

from __future__ import annotations

import contextlib
from pathlib import Path as _DoctorPath

import click
from rich.table import Table

from repowise.cli.helpers import (
    console,
    get_db_url_for_repo,
    get_repowise_dir,
    load_state,
    resolve_command_target,
    run_async,
)


def _check(name: str, ok: bool, detail: str = "") -> tuple[str, str, str]:
    status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
    return (name, status, detail)


def _print_cli_version_status() -> None:
    """Print a best-effort CLI update-check line.

    Advisory only: an outdated CLI is informational, not a broken repo, so this
    never affects doctor's pass/fail outcome and never fails on network errors.
    Runs once per invocation (the CLI version is global, not per-repo).
    """
    try:
        from repowise.cli.update_check import get_cli_update_check

        check = get_cli_update_check()
    except Exception:
        return  # never let the update check break doctor

    # Show the full running command and resolved path verbatim — they can
    # differ (e.g. a stale shim on PATH vs the venv that launched this process),
    # and surfacing that mismatch is the point of this row.
    path_detail = check.resolved_executable or "not on PATH"
    running = check.running_executable or "?"

    if check.latest_version is None:
        status = "[green]OK[/green]"
        detail = (
            f"current {check.current_version}, could not check latest version, "
            f"path {path_detail}, running {running}"
        )
    elif check.update_available:
        status = "[yellow]WARN[/yellow]"
        detail = (
            f"current {check.current_version}, latest {check.latest_version}, "
            f"path {path_detail}, running {running}"
        )
    else:
        status = "[green]OK[/green]"
        detail = f"current {check.current_version} (latest), path {path_detail}, running {running}"

    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column(style="cyan")
    table.add_column()
    table.add_column()
    table.add_row("CLI version", status, detail)
    console.print(table)

    if check.update_available:
        console.print(f"  [yellow]Update available:[/yellow] {check.suggested_command}")
        console.print("  [dim]Restart Claude/Codex/Cursor or any MCP client after updating.[/dim]")


def _run_repo_checks(repo_path: _DoctorPath, repair: bool) -> bool:
    """Run the standard health checks against one repo. Returns ``True`` if
    all checks passed.

    Extracted so workspace mode can iterate over every repo without
    duplicating the full check body.
    """
    checks: list[tuple[str, str, str]] = []

    # 1. Git repository?
    try:
        import git as gitpython

        gitpython.Repo(repo_path, search_parent_directories=True)
        checks.append(_check("Git repository", True, str(repo_path)))
    except Exception:
        checks.append(_check("Git repository", False, "Not a git repo"))

    # 2. .repowise/ exists?
    repowise_dir = get_repowise_dir(repo_path)
    checks.append(_check(".repowise/ directory", repowise_dir.exists(), str(repowise_dir)))

    # 3. Database connectable?
    db_path = repowise_dir / "wiki.db"
    db_ok = False
    page_count = 0
    if db_path.exists():
        try:

            async def _check_db():
                from repowise.core.persistence import (
                    create_engine,
                    create_session_factory,
                    get_repository_by_path,
                    get_session,
                    list_pages,
                )

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                sf = create_session_factory(engine)
                count = 0
                async with get_session(sf) as session:
                    repo = await get_repository_by_path(session, str(repo_path))
                    if repo:
                        pages = await list_pages(session, repo.id, limit=10000)
                        count = len(pages)
                await engine.dispose()
                return count

            page_count = run_async(_check_db())
            db_ok = True
        except Exception as e:
            checks.append(_check("Database", False, str(e)))
    if db_ok:
        checks.append(_check("Database", True, f"{page_count} pages"))
    elif not db_path.exists():
        checks.append(_check("Database", False, "wiki.db not found"))

    # 4. state.json valid?
    state = load_state(repo_path)
    state_ok = bool(state)
    checks.append(
        _check(
            "state.json",
            state_ok,
            f"last_sync: {(state.get('last_sync_commit') or '—')[:8]}"
            if state_ok
            else "Not found or empty",
        )
    )

    # 5. Provider importable?
    provider_ok = False
    try:
        from repowise.core.providers import list_providers

        providers = list_providers()
        provider_ok = len(providers) > 0
        checks.append(_check("Providers", provider_ok, ", ".join(providers)))
    except Exception as e:
        checks.append(_check("Providers", False, str(e)))

    # 6. Provider configuration?
    from repowise.cli.helpers import validate_provider_config

    config_warnings = validate_provider_config()
    config_ok = len(config_warnings) == 0
    config_detail = "All required API keys configured" if config_ok else "; ".join(config_warnings)
    checks.append(_check("Provider config", config_ok, config_detail))

    # 7. Stale page count
    stale_count = 0
    if db_ok and page_count > 0:
        try:

            async def _check_stale():
                from repowise.core.persistence import (
                    create_engine,
                    create_session_factory,
                    get_repository_by_path,
                    get_session,
                    get_stale_pages,
                )

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                sf = create_session_factory(engine)
                async with get_session(sf) as session:
                    repo = await get_repository_by_path(session, str(repo_path))
                    if repo:
                        stale = await get_stale_pages(session, repo.id)
                        await engine.dispose()
                        return len(stale)
                await engine.dispose()
                return 0

            stale_count = run_async(_check_stale())
            checks.append(_check("Stale pages", stale_count == 0, f"{stale_count} stale"))
        except Exception:
            checks.append(_check("Stale pages", True, "Could not check"))

    # 8-9. Three-store consistency (SQL vs Vector Store vs FTS)
    missing_from_vector: set[str] = set()
    orphaned_vector: set[str] = set()
    missing_from_fts: set[str] = set()
    orphaned_fts: set[str] = set()

    if db_ok and page_count > 0:
        try:

            async def _check_stores():
                from repowise.core.persistence import (
                    FullTextSearch,
                    create_engine,
                    create_session_factory,
                    get_repository_by_path,
                    get_session,
                    list_pages,
                )
                from repowise.core.persistence.vector_store import (
                    LanceDBVectorStore,
                )
                from repowise.core.providers.embedding.base import MockEmbedder

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                sf = create_session_factory(engine)

                # Get all SQL page IDs
                async with get_session(sf) as session:
                    repo = await get_repository_by_path(session, str(repo_path))
                    if not repo:
                        await engine.dispose()
                        return set(), set(), set(), set()
                    pages = await list_pages(session, repo.id, limit=10000)
                    sql_ids = {p.page_id for p in pages}

                # Check vector store
                vs_ids: set[str] = set()
                lance_dir = repowise_dir / "lancedb"
                if lance_dir.exists():
                    try:
                        embedder = MockEmbedder()
                        vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                        vs_ids = await vs.list_page_ids()
                        await vs.close()
                    except Exception:
                        pass  # LanceDB not available

                m_vec = sql_ids - vs_ids if vs_ids else set()
                o_vec = vs_ids - sql_ids if vs_ids else set()

                # Check FTS
                fts = FullTextSearch(engine)
                try:
                    fts_ids = await fts.list_indexed_ids()
                except Exception:
                    fts_ids = set()
                m_fts = sql_ids - fts_ids if fts_ids else set()
                o_fts = fts_ids - sql_ids if fts_ids else set()

                await engine.dispose()
                return m_vec, o_vec, m_fts, o_fts

            missing_from_vector, orphaned_vector, missing_from_fts, orphaned_fts = run_async(
                _check_stores()
            )

            vec_ok = not missing_from_vector and not orphaned_vector
            vec_detail = (
                "in sync"
                if vec_ok
                else (f"{len(missing_from_vector)} missing, {len(orphaned_vector)} orphaned")
            )
            checks.append(_check("SQL ↔ Vector Store", vec_ok, vec_detail))

            fts_ok = not missing_from_fts and not orphaned_fts
            fts_detail = (
                "in sync"
                if fts_ok
                else (f"{len(missing_from_fts)} missing, {len(orphaned_fts)} orphaned")
            )
            checks.append(_check("SQL ↔ FTS Index", fts_ok, fts_detail))
        except Exception:
            checks.append(_check("Store consistency", True, "Could not check"))

    # 10. AtomicStorageCoordinator drift check
    coord_drift: float | None = None
    coord_sql_pages: int | None = None
    coord_vector_count: int | None = None
    if db_ok:
        try:

            async def _check_coordinator():
                from repowise.core.persistence import (
                    create_engine,
                    create_session_factory,
                    get_session,
                )
                from repowise.core.persistence.coordinator import AtomicStorageCoordinator
                from repowise.core.persistence.vector_store import LanceDBVectorStore
                from repowise.core.providers.embedding.base import MockEmbedder

                url = get_db_url_for_repo(repo_path)
                engine = create_engine(url)
                sf = create_session_factory(engine)

                vector_store = None
                lance_dir = repowise_dir / "lancedb"
                if lance_dir.exists():
                    try:
                        embedder = MockEmbedder()
                        vector_store = LanceDBVectorStore(str(lance_dir), embedder=embedder)
                    except Exception:
                        pass

                async with get_session(sf) as session:
                    coord = AtomicStorageCoordinator(
                        session, graph_builder=None, vector_store=vector_store
                    )
                    result = await coord.health_check()

                if vector_store is not None:
                    with contextlib.suppress(Exception):
                        await vector_store.close()
                await engine.dispose()
                return result

            coord_result = run_async(_check_coordinator())
            coord_sql_pages = coord_result.get("sql_pages")
            coord_vector_count = coord_result.get("vector_count")
            coord_drift = coord_result.get("drift")

            drift_pct = f"{coord_drift * 100:.1f}%" if coord_drift is not None else "N/A"
            if coord_drift is None:
                drift_color = "white"
            elif coord_drift < 0.05:
                drift_color = "green"
            elif coord_drift < 0.15:
                drift_color = "yellow"
            else:
                drift_color = "red"

            vec_display = (
                str(coord_vector_count)
                if coord_vector_count != -1 and coord_vector_count is not None
                else "unknown"
            )
            drift_detail = (
                f"SQL={coord_sql_pages}, Vector={vec_display}, "
                f"Drift=[{drift_color}]{drift_pct}[/{drift_color}]"
            )
            coord_ok = coord_drift is None or coord_drift < 0.05
            checks.append(_check("Coordinator drift", coord_ok, drift_detail))
        except Exception as exc:
            checks.append(_check("Coordinator drift", True, f"Could not check: {exc}"))

    # 11. Distill — config block, omission store, rewrite hook (advisory)
    checks.extend(_distill_checks(repo_path))

    # Display
    table = Table(title=f"repowise Doctor — {repo_path.name}")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")
    for name, status, detail in checks:
        table.add_row(name, status, detail)
    console.print(table)

    all_ok = all("[green]OK[/green]" in status for _, status, _ in checks)
    if all_ok:
        console.print("[bold green]All checks passed![/bold green]")
    else:
        console.print("[bold yellow]Some checks failed.[/bold yellow]")

    # --repair: fix detected mismatches
    has_mismatches = missing_from_fts or orphaned_fts or missing_from_vector or orphaned_vector
    if repair and has_mismatches:
        console.print("\n[bold]Repairing store mismatches...[/bold]")

        async def _repair():
            from repowise.core.persistence import (
                FullTextSearch,
                create_engine,
                create_session_factory,
                get_session,
            )

            url = get_db_url_for_repo(repo_path)
            engine = create_engine(url)
            sf = create_session_factory(engine)
            repaired = 0

            # Repair FTS: re-index missing pages, delete orphaned
            if missing_from_fts or orphaned_fts:
                fts = FullTextSearch(engine)
                await fts.ensure_index()
                if missing_from_fts:
                    # Fetch full page data for missing pages
                    async with get_session(sf) as session:
                        from sqlalchemy import select

                        from repowise.core.persistence.models import Page

                        rows = await session.execute(
                            select(Page).where(Page.page_id.in_(list(missing_from_fts)))
                        )
                        for page in rows.scalars().all():
                            await fts.index(page.page_id, page.title, page.content)
                            repaired += 1
                for pid in orphaned_fts:
                    await fts.delete(pid)
                    repaired += 1

            # Repair vector store: re-embed missing pages, delete orphaned
            lance_dir = repowise_dir / "lancedb"
            if lance_dir.exists() and (missing_from_vector or orphaned_vector):
                try:
                    from repowise.core.persistence.vector_store import LanceDBVectorStore
                    from repowise.core.providers.embedding.base import MockEmbedder

                    # Use mock embedder for repair to avoid API costs;
                    # user can re-run `repowise reindex` for real embeddings
                    embedder = MockEmbedder()

                    vs = LanceDBVectorStore(str(lance_dir), embedder=embedder)

                    if missing_from_vector:
                        async with get_session(sf) as session:
                            from sqlalchemy import select

                            from repowise.core.persistence.models import Page

                            rows = await session.execute(
                                select(Page).where(Page.page_id.in_(list(missing_from_vector)))
                            )
                            for page in rows.scalars().all():
                                await vs.embed_and_upsert(
                                    page.page_id,
                                    page.content,
                                    {
                                        "title": page.title,
                                        "page_type": page.page_type,
                                        "target_path": page.target_path,
                                    },
                                )
                                repaired += 1

                    for pid in orphaned_vector:
                        await vs.delete(pid)
                        repaired += 1

                    await vs.close()
                except Exception as exc:
                    console.print(f"[yellow]Vector repair skipped: {exc}[/yellow]")

            await engine.dispose()
            return repaired

        repaired_count = run_async(_repair())
        console.print(f"[bold green]Repaired {repaired_count} entries.[/bold green]")
    elif repair and not has_mismatches:
        console.print("[green]Nothing to repair.[/green]")

    return all_ok


def _distill_checks(repo_path: _DoctorPath) -> list[tuple[str, str, str]]:
    """Distill feature health: config validity, store sizing, rewrite hook.

    The rewrite hook is opt-in, so its absence is informational, never a
    failure. Any unexpected error degrades to an advisory OK row — distill
    problems must not break doctor.
    """
    checks: list[tuple[str, str, str]] = []

    # Config block valid?
    distill_cfg = None
    try:
        from repowise.core.repo_config import load_repo_config

        distill_cfg = load_repo_config(repo_path).get("distill")
        from repowise.core.distill.config import validate_distill_config

        problems = validate_distill_config(distill_cfg)
        if problems:
            checks.append(_check("Distill config", False, "; ".join(problems)))
        else:
            checks.append(
                _check("Distill config", True, "valid" if distill_cfg else "defaults (no block)")
            )
    except Exception as exc:
        checks.append(_check("Distill config", True, f"Could not check: {exc}"))

    # Omission store sizing (TTL + cap are pruned opportunistically on write,
    # so a store far past its cap means pruning is not keeping up).
    try:
        from repowise.core.distill.config import omission_store_settings

        _ttl_days, max_mb = omission_store_settings(distill_cfg)
        db_path = repo_path / ".repowise" / "omissions" / "omissions.db"
        if not db_path.is_file():
            checks.append(_check("Omission store", True, "not created yet"))
        else:
            size_bytes = db_path.stat().st_size
            wal = db_path.with_name(db_path.name + "-wal")
            if wal.is_file():
                size_bytes += wal.stat().st_size
            size_mb = size_bytes / (1024 * 1024)
            ok = size_mb <= max_mb * 1.5  # WAL slack before checkpointing
            detail = f"{size_mb:.1f} MB (cap {max_mb:g} MB)"
            if not ok:
                detail += " — over cap; pruning happens on the next distill write"
            checks.append(_check("Omission store", ok, detail))
    except Exception as exc:
        checks.append(_check("Omission store", True, f"Could not check: {exc}"))

    # Rewrite hook (advisory: strictly opt-in).
    try:
        from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter

        installed = ClaudeCodeAdapter().rewrite_hook_installed()
        if installed:
            commands_cfg = distill_cfg.get("commands") if isinstance(distill_cfg, dict) else None
            opted_out = isinstance(commands_cfg, dict) and commands_cfg.get("enabled") is False
            detail = "installed (this repo opted out)" if opted_out else "installed"
        else:
            detail = "not installed (opt-in: repowise hook rewrite install)"
        checks.append(_check("Distill rewrite hook", True, detail))
    except Exception as exc:
        checks.append(_check("Distill rewrite hook", True, f"Could not check: {exc}"))

    return checks


@click.command("doctor")
@click.argument("path", required=False, default=None)
@click.option("--repair", is_flag=True, default=False, help="Attempt to fix detected mismatches.")
@click.option(
    "--workspace",
    "-w",
    is_flag=True,
    default=False,
    help="Force workspace mode (run checks against every repo in the workspace).",
)
@click.option(
    "--no-workspace",
    is_flag=True,
    default=False,
    help="Force single-repo mode even when invoked from a workspace.",
)
def doctor_command(
    path: str | None,
    repair: bool,
    workspace: bool,
    no_workspace: bool,
) -> None:
    """Run health checks on the wiki setup.

    Auto-detects workspace mode when invoked from a workspace root. In
    workspace mode, runs the full check battery against each indexed repo
    and prints a per-repo table plus a workspace-level summary.
    """
    target = resolve_command_target(
        path=path,
        workspace_flag=workspace,
        no_workspace_flag=no_workspace,
    )
    target.notice(console, command="doctor")

    # Advisory CLI update check — printed once, above the repo check table(s).
    _print_cli_version_status()

    if not target.is_workspace:
        assert target.repo_path is not None
        _run_repo_checks(target.repo_path, repair)
        return

    # Workspace mode — iterate over every entry, run workspace-level
    # validation, and report a summary table at the end so the user knows
    # which repos need attention.
    assert target.ws_root is not None and target.ws_config is not None
    ws_root = target.ws_root
    ws_config = target.ws_config

    ws_issues = _run_workspace_checks(ws_root, ws_config, repair=repair)

    overall_ok = True
    not_indexed: list[str] = []
    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()
        if not abs_path.is_dir():
            continue
        if not (abs_path / ".repowise").is_dir():
            not_indexed.append(entry.alias)
            continue
        console.print()
        console.print(
            f"[bold]── {entry.alias}[/bold]  "
            f"[dim]({entry.path})[/dim]"
            + (
                "  [bold cyan](primary)[/bold cyan]"
                if entry.alias == ws_config.default_repo
                else ""
            )
        )
        ok = _run_repo_checks(abs_path, repair)
        overall_ok = overall_ok and ok

    console.print()
    if not_indexed:
        console.print(f"[yellow]Not indexed:[/yellow] {', '.join(not_indexed)}")
        console.print("  Run [bold]repowise update --workspace[/bold] to index them.")
    if ws_issues and not repair:
        console.print(
            f"[yellow]{len(ws_issues)} workspace-level issue(s); "
            f"rerun with [bold]--repair[/bold] to attempt fixes.[/yellow]"
        )

    workspace_clean = not ws_issues and overall_ok and not not_indexed
    if workspace_clean:
        console.print("[bold green]Workspace healthy.[/bold green]")
    elif overall_ok and not ws_issues:
        console.print("[bold yellow]All indexed repos healthy; some repos unindexed.[/bold yellow]")
    else:
        console.print("[bold yellow]Some checks failed across the workspace.[/bold yellow]")


def _run_workspace_checks(
    ws_root: _DoctorPath,
    ws_config,
    *,
    repair: bool,
) -> list[str]:
    """Run workspace-level validation. Returns a list of issue strings.

    Covers:
      - Per-entry directory existence & ``.git`` presence.
      - State drift between ``WorkspaceConfig.last_commit_at_index`` and
        each repo's ``.repowise/state.json``.
      - MCP server registration (best-effort detection in claude config).
      - ``--repair``: rebuild missing ``state.json``, drop dead workspace
        entries (with a notice).
    """
    from repowise.core.workspace.update import (
        read_state_commit,
        sync_workspace_state_from_disk,
    )

    rows: list[tuple[str, str, str]] = []
    issues: list[str] = []

    dead_entries: list[str] = []
    for entry in ws_config.repos:
        abs_path = (ws_root / entry.path).resolve()

        # Dir & git presence
        if not abs_path.is_dir():
            rows.append((entry.alias, "[red]MISSING[/red]", f"directory not found: {entry.path}"))
            dead_entries.append(entry.alias)
            issues.append(f"{entry.alias}: missing directory")
            continue
        if not (abs_path / ".git").exists():
            rows.append((entry.alias, "[yellow]WARN[/yellow]", "not a git repo"))
            issues.append(f"{entry.alias}: not a git repo")

        # State drift
        disk_commit = read_state_commit(abs_path)
        cfg_commit = entry.last_commit_at_index
        if disk_commit and cfg_commit and disk_commit != cfg_commit:
            rows.append(
                (
                    entry.alias,
                    "[yellow]DRIFT[/yellow]",
                    f"config={cfg_commit[:8]}, state.json={disk_commit[:8]}",
                )
            )
            issues.append(f"{entry.alias}: workspace config / state.json drift")
        elif disk_commit and not cfg_commit:
            rows.append(
                (
                    entry.alias,
                    "[yellow]DRIFT[/yellow]",
                    f"workspace config missing last_commit_at_index (state.json has {disk_commit[:8]})",
                )
            )
            issues.append(f"{entry.alias}: workspace config missing commit pointer")
        elif (abs_path / ".repowise").is_dir() and not disk_commit:
            rows.append(
                (
                    entry.alias,
                    "[yellow]WARN[/yellow]",
                    "state.json missing or empty (run `repowise update`)",
                )
            )
            issues.append(f"{entry.alias}: missing state.json")
        else:
            rows.append((entry.alias, "[green]OK[/green]", entry.path))

    table = Table(title="repowise Workspace Doctor")
    table.add_column("Repo", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")
    for r in rows:
        table.add_row(*r)
    console.print(table)

    # MCP server registration — best-effort, advisory only.
    _check_mcp_registered(ws_root)

    # --repair: sync the workspace config from disk and drop dead entries.
    if repair:
        changed = sync_workspace_state_from_disk(ws_root, ws_config)
        if changed:
            console.print(
                f"[green]Repaired workspace config from disk for:[/green] {', '.join(changed)}"
            )
        if dead_entries:
            console.print(
                f"[yellow]Removing dead workspace entries:[/yellow] {', '.join(dead_entries)}"
            )
            for alias in dead_entries:
                ws_config.remove_repo(alias)
            ws_config.save(ws_root)
            console.print("[green]Workspace config updated.[/green]")
        if not changed and not dead_entries:
            console.print("[green]No workspace-level repairs needed.[/green]")

    return issues


def _check_mcp_registered(ws_root: _DoctorPath) -> None:
    """Best-effort check that a Claude MCP entry points at this workspace.

    The check is advisory: a missing entry is not an error, since the user
    may use the HTTP server or a different MCP client. We just print a
    helpful hint so the workspace can be wired up if the user wants it.
    """
    import json as _json
    import os as _os

    candidates: list[_DoctorPath] = []
    appdata = _os.environ.get("APPDATA")
    if appdata:
        candidates.append(_DoctorPath(appdata) / "Claude" / "claude_desktop_config.json")
    home = _DoctorPath.home()
    candidates.extend(
        [
            home / ".claude" / "claude_desktop_config.json",
            home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
            home / ".config" / "Claude" / "claude_desktop_config.json",
        ]
    )

    found_paths: list[str] = []
    for cfg in candidates:
        if not cfg.is_file():
            continue
        try:
            data = _json.loads(cfg.read_text(encoding="utf-8"))
        except Exception:
            continue
        servers = data.get("mcpServers", {}) or {}
        for name, spec in servers.items():
            args = spec.get("args", []) if isinstance(spec, dict) else []
            arg_str = " ".join(str(a) for a in args)
            if str(ws_root) in arg_str or str(ws_root.resolve()) in arg_str:
                found_paths.append(f"{cfg.name}:{name}")

    if found_paths:
        console.print(f"  [dim]MCP: registered ({', '.join(found_paths)})[/dim]")
    else:
        console.print(
            "  [dim]MCP: no claude_desktop_config.json entry found — run "
            "`repowise hook install` to register.[/dim]"
        )
