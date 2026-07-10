"""Per-repo doctor checks: store consistency, drift, distill, and repair."""

from __future__ import annotations

import contextlib
from pathlib import Path as _DoctorPath

from rich.table import Table

from repowise.cli.helpers import (
    console,
    get_db_url_for_repo,
    get_repowise_dir,
    load_state,
    run_async,
)

from ._types import DoctorCheck, _check, _status_markup
from .advisories import _advise_claude_md_stamp


async def _decision_vector_ids(session, repository_id: str) -> set[str]:
    """Vector-store ids for this repo's decision records.

    Decisions are co-located in the *page* vector store under the
    ``decision:<record_id>`` namespace (no separate table). The store therefore
    legitimately holds page vectors *and* decision vectors, so any SQL↔vector
    reconciliation must count decisions on the SQL side — otherwise every
    decision embedding reads as an orphan.
    """
    from sqlalchemy import text as _sql_text

    from repowise.core.analysis.decisions.semantic_match import DECISION_VECTOR_PREFIX

    rows = await session.execute(
        _sql_text("SELECT id FROM decision_records WHERE repository_id = :rid"),
        {"rid": repository_id},
    )
    return {f"{DECISION_VECTOR_PREFIX}{r[0]}" for r in rows}


def _run_repo_checks(
    repo_path: _DoctorPath, repair: bool, *, fmt: str = "table"
) -> tuple[bool, list[DoctorCheck]]:
    """Run the standard health checks against one repo.

    Returns ``(all_ok, checks)``. Extracted so workspace mode can iterate
    over every repo without duplicating the full check body. When
    ``fmt != "table"`` the table and advisory lines are not printed (repair
    is also skipped; callers must not pass ``repair=True`` with a
    non-table format).
    """
    checks: list[DoctorCheck] = []

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

    # 6b. Hosted account (informational: signed out is not a failure).
    try:
        from repowise.cli.platform import auth, credentials

        creds = credentials.load()
        if creds is None:
            checks.append(
                _check("Hosted account", True, "Not signed in (optional: repowise login)")
            )
        elif creds.get("stale"):
            checks.append(
                _check("Hosted account", False, "Session expired or revoked; run repowise login")
            )
        else:
            account = auth.fetch_account()
            cached = (account or creds.get("account") or {}) or {}
            who = cached.get("github_username") or cached.get("email") or "unknown"
            tier = cached.get("tier") or "free"
            suffix = "" if account else " (offline, using cached identity)"
            checks.append(_check("Hosted account", True, f"{who} ({tier}){suffix}"))
    except Exception as e:
        checks.append(_check("Hosted account", False, str(e)))

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
                    # The page vector store also holds decision embeddings under
                    # the "decision:<id>" namespace, so they belong on the SQL
                    # side of the ORPHAN check (but NOT FTS, which only indexes
                    # pages). They are deliberately excluded from the MISSING
                    # check: a decision can legitimately have no vector (empty
                    # match text, swallowed embed failure) and repair cannot
                    # re-embed it, so flagging it would be permanent noise.
                    vector_sql_ids = sql_ids | await _decision_vector_ids(session, repo.id)

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
                o_vec = vs_ids - vector_sql_ids if vs_ids else set()

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
    coord_drift_color = "white"
    coord_drift_pct = "N/A"
    if db_ok:
        try:

            async def _check_coordinator():
                from repowise.core.persistence import (
                    create_engine,
                    create_session_factory,
                    get_repository_by_path,
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

                    # The coordinator's drift compares wiki_pages count against
                    # the vector count, but the vector store also holds decision
                    # embeddings ("decision:<id>" namespace). Without counting
                    # decisions on the SQL side, every decision reads as drift.
                    # Recompute drift with a decision-aware SQL count so a
                    # consistent store reports ~0% rather than a false FAIL.
                    # Count only decisions that actually have a vector: a
                    # decision can legitimately be unembedded (empty match
                    # text, swallowed embed failure), and counting it would
                    # bias drift positive (SQL > Vector) forever.
                    repo = await get_repository_by_path(session, str(repo_path))
                    if repo is not None:
                        dec_ids = await _decision_vector_ids(session, repo.id)
                        decision_count = len(dec_ids)
                        if vector_store is not None:
                            with contextlib.suppress(Exception):
                                store_ids = await vector_store.list_page_ids()
                                decision_count = len(dec_ids & store_ids)
                        sql_pages = result.get("sql_pages")
                        vector_count = result.get("vector_count")
                        if sql_pages is not None:
                            adjusted_sql = sql_pages + decision_count
                            result["sql_pages"] = adjusted_sql
                            if vector_count is not None and vector_count != -1:
                                denom = max(adjusted_sql, 1)
                                result["drift"] = abs(adjusted_sql - vector_count) / denom

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
            drift_detail = f"SQL={coord_sql_pages}, Vector={vec_display}, Drift={drift_pct}"
            coord_ok = coord_drift is None or coord_drift < 0.05
            checks.append(_check("Coordinator drift", coord_ok, drift_detail))
            coord_drift_color = drift_color
            coord_drift_pct = drift_pct
        except Exception as exc:
            checks.append(_check("Coordinator drift", True, f"Could not check: {exc}"))

    # 11. Distill — config block, omission store, rewrite hook (advisory)
    checks.extend(_distill_checks(repo_path))

    # 12. Claude Code MCP registration: wedged-path detection
    registration_check, registration_wedged = _claude_registration_check()
    checks.append(registration_check)

    all_ok = all(c.ok for c in checks)

    if fmt != "table":
        return all_ok, checks

    # Display: the "Coordinator drift" row's detail gets its drift
    # percentage colored here, since that highlight is orthogonal to the
    # row's own OK/FAIL status.
    table = Table(title=f"repowise Doctor — {repo_path.name}")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
    table.add_column("Detail")
    for c in checks:
        detail = c.detail
        if c.name == "Coordinator drift":
            detail = detail.replace(
                f"Drift={coord_drift_pct}",
                f"Drift=[{coord_drift_color}]{coord_drift_pct}[/{coord_drift_color}]",
            )
        table.add_row(c.name, _status_markup(c.ok), detail)
    console.print(table)

    # CLAUDE.md freshness stamp — advisory, never fails the run. The managed
    # ".claude/CLAUDE.md" block stamps the commit it was generated against; if
    # editor-file regeneration stops, the stamp freezes while the index moves
    # on and agents reading the stale "Last indexed" line distrust the tools.
    _advise_claude_md_stamp(repo_path, state)

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
    elif repair and not has_mismatches and not registration_wedged:
        console.print("[green]Nothing to repair.[/green]")

    if repair and registration_wedged:
        from repowise.cli.editor_integrations.claude_config import register_with_claude_code

        fixed = register_with_claude_code(repo_path)
        if fixed:
            console.print(
                f"[bold green]Re-registered the Claude Code MCP entry ({fixed}).[/bold green]"
            )
        else:
            console.print(
                "[yellow]Could not re-register the Claude Code MCP entry; "
                "run `repowise init` to redo editor setup.[/yellow]"
            )

    return all_ok, checks


def _claude_registration_check() -> tuple[DoctorCheck, bool]:
    """Detect a wedged Claude Code MCP registration (stale paths).

    The global ``~/.claude/settings.json`` entry can end up pointing at a
    directory that no longer exists (a moved repo, or a leaked temp path) or
    at a pinned command binary from a deleted venv; either way the MCP
    server silently fails to start in every Claude Code session. Returns
    ``(check, wedged)``; ``wedged`` drives the ``--repair`` re-registration.
    Absence of a registration is informational, never a failure.
    """
    name = "Claude Code MCP entry"
    try:
        import json as _json

        from repowise.cli.editor_integrations.claude_config import _claude_code_settings_path

        settings_path = _claude_code_settings_path()
        if not settings_path.exists():
            return _check(name, True, "not registered (repowise init registers it)"), False
        try:
            settings = _json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            return _check(name, True, f"could not parse {settings_path}"), False
        servers = settings.get("mcpServers") if isinstance(settings, dict) else None
        entry = servers.get("repowise") if isinstance(servers, dict) else None
        if not isinstance(entry, dict):
            return _check(name, True, "not registered (repowise init registers it)"), False

        problems: list[str] = []
        command = entry.get("command")
        # Bare command names resolve via PATH at session start; only a pinned
        # absolute path can go stale.
        if (
            isinstance(command, str)
            and ("/" in command or "\\" in command)
            and not _DoctorPath(command).exists()
        ):
            problems.append(f"command not found: {command}")
        target = _registration_target(entry)
        if target is not None and not _DoctorPath(target).is_dir():
            problems.append(f"registered path missing: {target}")
        if problems:
            return _check(name, False, "; ".join(problems) + " (rerun with --repair)"), True
        return _check(name, True, target or "registered"), False
    except Exception as exc:
        return _check(name, True, f"Could not check: {exc}"), False


def _registration_target(entry: dict) -> str | None:
    """The repo/workspace path a registration serves, or None if unrecognized.

    Registrations are shaped ``args: ["mcp", "<abs path>", "--transport",
    "stdio"]``; the target sits right after ``mcp``. Anything else (a flag
    in that slot, a hand-edited entry) returns None rather than guessing.
    """
    args = entry.get("args")
    if not isinstance(args, list) or args[:1] != ["mcp"] or len(args) < 2:
        return None
    target = args[1]
    if isinstance(target, str) and not target.startswith("-"):
        return target
    return None


def _distill_checks(repo_path: _DoctorPath) -> list[DoctorCheck]:
    """Distill feature health: config validity, store sizing, rewrite hook.

    The rewrite hook is opt-in, so its absence is informational, never a
    failure. Any unexpected error degrades to an advisory OK row — distill
    problems must not break doctor.
    """
    checks: list[DoctorCheck] = []

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
        from repowise.cli.agent_adapters.codex import CodexAdapter

        surfaces = [("claude-code", ClaudeCodeAdapter())]
        codex = CodexAdapter()
        if codex.detect():
            surfaces.append(("codex", codex))
        installed_names = [name for name, a in surfaces if a.rewrite_hook_installed()]
        if installed_names:
            commands_cfg = distill_cfg.get("commands") if isinstance(distill_cfg, dict) else None
            opted_out = isinstance(commands_cfg, dict) and commands_cfg.get("enabled") is False
            detail = ", ".join(installed_names)
            detail += " (this repo opted out)" if opted_out else ""
            detail = f"installed: {detail}"
        else:
            detail = "not installed (opt-in: repowise hook rewrite install)"
        checks.append(_check("Distill rewrite hook", True, detail))
    except Exception as exc:
        checks.append(_check("Distill rewrite hook", True, f"Could not check: {exc}"))

    return checks
