"""Worktree seeding: start a linked worktree's index from its base checkout."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from repowise.cli.errors import reasoned_error
from repowise.cli.helpers import console
from repowise.cli.ui import OK

from ._options import InitOptions


def _seed_base(opts: InitOptions, repo_path: Path) -> Path | None:
    """The checkout to seed from, or None for an ordinary init.

    Explicit --seed-from wins. Otherwise, an unindexed linked worktree
    auto-seeds from its base checkout (derived via --git-common-dir, no path
    needed) when that base holds a healthy index. --no-seed forces a cold init.
    """
    from repowise.cli.worktree import base_is_seedable, detect_worktree_base

    seed_base: Path | None = None
    if opts.seed_from:
        seed_base = Path(opts.seed_from).resolve()
        if seed_base == repo_path.resolve():
            raise reasoned_error(
                "--seed-from cannot be the same as the target directory.",
                reason="seed_from_is_target",
            )
    elif not opts.no_seed and not (repo_path / ".repowise" / "state.json").exists():
        detected = detect_worktree_base(repo_path)
        if detected is not None and base_is_seedable(detected):
            seed_base = detected
            if not opts.force:
                console.print(
                    f"This is a linked worktree of {detected}, which is already "
                    "indexed. init copies that index and catches it up with "
                    "[bold]repowise update[/bold] instead of indexing from scratch.\n"
                    "[dim]Pass --no-seed for a full init here.[/dim]"
                )

    # ``--force`` asks for the re-index seeding exists to avoid, so the two do
    # not combine. Delegating to ``run_update`` with ``full=force`` would mean
    # ``update --full``: the fast -> full upgrade that resolves a provider and
    # regenerates the whole wiki with a model (#1482). Dropping the seed lets
    # the ordinary init path run, which is what ``init --force`` does outside a
    # worktree. ``update --full`` stays the only paid path.
    if seed_base is not None and opts.force:
        requested = "the index at" if not opts.seed_from else "the requested seed"
        console.print(
            f"[dim]\\[worktree][/dim] --force re-indexes this checkout from "
            f"scratch, so {requested} {seed_base} is not used."
        )
        seed_base = None
    return seed_base


def _run_seeded_update(opts: InitOptions, repo_path: Path, scan: Any) -> None:
    """Catch the seeded index up with ``repowise update``."""
    from repowise.cli.commands.update_cmd.command import run_update
    from repowise.core.persistence.database import DB_ENV_VARS

    is_workspace = len(scan.repos) > 1 and not opts.no_workspace

    # Strip db-pin env vars to enforce worktree DB resolution (note: potential process-global race condition).
    old_db_urls = {}
    for env_key in DB_ENV_VARS:
        if env_key in os.environ:
            old_db_urls[env_key] = os.environ.pop(env_key)

    try:
        run_update(
            path=str(repo_path),
            provider_name=opts.provider_name,
            model=opts.model,
            since=None,
            reasoning=opts.reasoning,
            cascade_budget=None,
            dry_run=opts.dry_run,
            workspace=is_workspace,
            no_workspace=opts.no_workspace,
            repo_alias=None,
            index_only=opts.index_only,
            docs_flag=None,
            # Never ``force``: ``full`` means ``update --full``, provider
            # resolution plus whole-repo model regeneration. A ``--force`` run
            # never reaches here (the seed is dropped), so this is a constant.
            full=False,
            agents_md=opts.agents_md,
            concurrency=opts.concurrency,
            no_cost_tracking=opts.no_cost_tracking,
            verbose=opts.verbose,
            progress=opts.progress,
        )
    finally:
        os.environ.update(old_db_urls)


def delegate_to_seeded_update(opts: InitOptions, repo_path: Path, scan: Any) -> bool:
    """Seed from the base checkout and hand off to update. True when init is done.

    Any failed validation falls back to a full init (returns False).
    """
    from repowise.cli.worktree import seed_index_from_base

    seed_base = _seed_base(opts, repo_path)
    if seed_base is None:
        return False
    seed_root = scan.root if getattr(scan, "root", None) else repo_path
    seeded = seed_index_from_base(
        root=seed_root,
        repo_paths=[r.path for r in scan.repos],
        seed_base=seed_base,
        include_submodules=opts.include_submodules,
        dry_run=opts.dry_run,
    )
    if not seeded:
        return False
    if opts.dry_run:
        console.print("[dim]Dry run: Would seed worktree index and delegate to update.[/dim]")
        return True
    console.print(
        f"[{OK}]Index copied from {seed_base}.[/] Running [bold]repowise update[/bold] "
        "to catch it up."
    )
    _run_seeded_update(opts, repo_path, scan)
    return True
