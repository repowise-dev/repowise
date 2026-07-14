"""Git-worktree detection and index seeding.

Shared by ``repowise init`` (explicit ``--seed-from`` and auto-detection) and
``repowise update`` (auto-seed of an unindexed worktree). A linked worktree
already knows its base checkout: its ``.git`` is a file whose common dir lives
under the base checkout's ``.git``, so seeding needs no user-supplied path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from repowise.cli.helpers import console

_SEED_TEMPDIR_STALENESS_SECS = 3600


def _git_output(args: list[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=cwd, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def detect_worktree_base(repo_path: Path) -> Path | None:
    """Return the base checkout of a linked git worktree, else None.

    A linked worktree has ``.git`` as a file; ``--git-common-dir`` then points
    at the base checkout's ``.git`` directory. Submodules also use a ``.git``
    file, but their common dir is ``<parent>/.git/modules/<name>``, so the
    ``.git``-named-parent check filters them out along with bare repos.
    """
    if not (repo_path / ".git").is_file():
        return None
    common_dir = _git_output(["rev-parse", "--git-common-dir"], repo_path)
    if not common_dir:
        return None
    common = Path(common_dir)
    if not common.is_absolute():
        common = (repo_path / common).resolve()
    if common.name != ".git":
        return None
    base = common.parent
    if base.resolve() == repo_path.resolve():
        return None
    return base


def base_is_seedable(base: Path) -> bool:
    """True when a checkout has the index artifacts seeding copies."""
    return (base / ".repowise" / "state.json").exists() and (
        base / ".repowise" / "wiki.db"
    ).exists()


def sweep_stale_seed_backups(paths: list[Path]) -> None:
    """Remove leftovers from previously disrupted seeds."""
    now = time.time()
    for root in paths:
        for glob_pattern in (".repowise.bak.*", ".repowise-seed-*"):
            for p in root.glob(glob_pattern):
                if p.is_dir():
                    try:
                        # Windows directory mtimes may not update on file creation; 1-hour threshold mitigates this.
                        mtime = p.stat().st_mtime
                        if now - mtime > _SEED_TEMPDIR_STALENESS_SECS:
                            shutil.rmtree(p, ignore_errors=True)
                    except OSError:
                        pass


def _get_initial_commit(p: Path) -> str:
    return _git_output(["rev-list", "--max-parents=0", "HEAD"], p)


def _adopt_repository_identity(repowise_dir: Path, *, src_repo: Path, dest_repo: Path) -> None:
    """Point the copied wiki.db's repository row at the seeded checkout.

    ``upsert_repository`` matches by ``local_path``. Left untouched, the first
    update in the worktree upserts a second repository row named after the
    worktree dir, and every regenerated page lands under it while the seeded
    pages stay under the base row — prior-page reuse and repo-scoped queries
    both silently split. Rewriting name + local_path (and retargeting the two
    repo-level pages that are keyed by repo name) makes the copy fully the
    worktree's own. Best-effort: an unmatched row means the base index is
    unusual, and falling through leaves today's (pre-fix) behavior.
    """
    import sqlite3
    from contextlib import closing

    db = repowise_dir / "wiki.db"
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute(
            "SELECT id, name FROM repositories WHERE local_path = ?", (str(src_repo),)
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id, name FROM repositories WHERE name = ?", (src_repo.name,)
            ).fetchone()
        if row is None:
            return
        repo_id, old_name = row
        conn.execute(
            "UPDATE repositories SET name = ?, local_path = ? WHERE id = ?",
            (dest_repo.name, str(dest_repo), repo_id),
        )
        conn.execute(
            "UPDATE wiki_pages SET target_path = ? "
            "WHERE repository_id = ? AND target_path = ? "
            "AND page_type IN ('repo_overview', 'architecture_diagram')",
            (dest_repo.name, repo_id, old_name),
        )
        conn.commit()


def seed_index_from_base(
    *,
    root: Path,
    repo_paths: list[Path],
    seed_base: Path,
    include_submodules: bool | None = None,
    dry_run: bool = False,
) -> bool:
    """Copy ``.repowise/`` from a base checkout into each target repo.

    Validates per repo (index artifacts present, shared initial commit,
    seed's last_sync_commit is an ancestor of the target HEAD), stages the
    copy into a temp dir, then swaps it in with a rename so a crash mid-copy
    never leaves a half-written index. All-or-nothing across ``repo_paths``:
    one failed validation rolls back every staged copy and returns False so
    the caller falls back to a full init.

    ``include_submodules`` is the caller's CLI flag; pass None to skip the
    conflict warning (update has no such flag).
    """
    sweep_stale_seed_backups([root, *repo_paths])

    temp_dirs: list[tuple[Path, Path]] = []
    success = True

    for r_path in repo_paths:
        r_rel = r_path.relative_to(root)
        src_repo = seed_base / r_rel

        if not base_is_seedable(src_repo):
            console.print(
                f"[yellow]Seed source {src_repo} is missing .repowise state/db. "
                f"Falling back to full init.[/yellow]"
            )
            success = False
            break

        if _get_initial_commit(src_repo) != _get_initial_commit(r_path) or not _get_initial_commit(
            src_repo
        ):
            console.print(
                f"[yellow]Seed source {src_repo} does not share the same initial "
                f"commit as worktree. Falling back to full init.[/yellow]"
            )
            success = False
            break

        state_data = json.loads((src_repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
        last_sync_commit = state_data.get("last_sync_commit")
        if not last_sync_commit:
            console.print(
                f"[yellow]Seed source {src_repo} has no last_sync_commit. "
                f"Falling back to full init.[/yellow]"
            )
            success = False
            break

        try:
            subprocess.check_call(
                ["git", "merge-base", "--is-ancestor", last_sync_commit, "HEAD"],
                cwd=r_path,
                stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            console.print(
                f"[yellow]Seed source {src_repo} last_sync_commit "
                f"{last_sync_commit[:8]} is not an ancestor of worktree HEAD. "
                f"Falling back to full init.[/yellow]"
            )
            success = False
            break

        source_head = _git_output(["rev-parse", "HEAD"], src_repo)
        if source_head and last_sync_commit != source_head:
            console.print(
                f"[dim]Note: Seed source {src_repo} is behind its HEAD, seeding "
                f"from last synced commit {last_sync_commit[:8]}.[/dim]"
            )

        if dry_run:
            continue

        try:
            temp_dir = Path(tempfile.mkdtemp(prefix=".repowise-seed-", dir=r_path))
            temp_dirs.append((r_path, temp_dir))

            shutil.copytree(src_repo / ".repowise", temp_dir, dirs_exist_ok=True)

            # Since config.yaml is copied atomically alongside state.json, the
            # config_fingerprint remains valid.
            st_data = json.loads((temp_dir / "state.json").read_text(encoding="utf-8"))

            if include_submodules is not None:
                state_include = st_data.get("include_submodules", False)
                if include_submodules != state_include:
                    console.print(
                        f"[yellow]Warning: --include-submodules={include_submodules} "
                        f"conflicts with copied state ({state_include}). Seeded state "
                        f"will take precedence.[/yellow]"
                    )

            (temp_dir / "state.json").write_text(json.dumps(st_data, indent=2), encoding="utf-8")

            _adopt_repository_identity(temp_dir, src_repo=src_repo, dest_repo=r_path)
        except Exception:
            success = False
            break

    if not success:
        for _, temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return False

    if dry_run:
        return True

    backups_created: list[tuple[Path, Path]] = []
    renamed_targets: list[Path] = []
    
    try:
        # Pass 1: backup existing
        for r_path, _ in temp_dirs:
            target = r_path / ".repowise"
            if target.exists():
                backup = r_path / f".repowise.bak.{uuid.uuid4().hex[:8]}"
                target.rename(backup)
                backups_created.append((target, backup))
                
        # Pass 2: rename temp to target
        for r_path, temp_dir in temp_dirs:
            target = r_path / ".repowise"
            temp_dir.rename(target)
            renamed_targets.append(target)
            
        # Pass 3: clean backups
        for _, backup in backups_created:
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        # Rollback target renames
        for target in renamed_targets:
            shutil.rmtree(target, ignore_errors=True)
        # Rollback backups
        for target, backup in backups_created:
            if backup.exists():
                backup.rename(target)
        # Clean temp dirs
        for _, temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return True
