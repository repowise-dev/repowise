"""The workspace config a workspace init writes, merged with the one already there.

Picking a subset of repos on a re-run must not silently shrink the workspace:
repos the user did not pick stay listed unless an interactive user asks to
replace the list, and the existing primary stays unless the user picked one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.markup import escape

from repowise.cli.ui import OK, WARN
from repowise.core.ci.markdown import plural

_NAMES_SHOWN = 5


def build_workspace_config(
    selected: list[Any],
    root: Path,
    primary_alias: str,
    *,
    console: Console,
    ask: bool,
    primary_picked: bool,
) -> Any:
    """The config for *selected*, keeping what an existing config lists.

    *ask* allows a prompt before repos the user did not pick are dropped;
    without it they are always kept. *primary_picked* means the user chose
    *primary_alias* in a prompt, so it replaces the existing primary.
    """
    from repowise.core.workspace import RepoEntry, WorkspaceConfig

    existing = _load_existing(root, console)
    previous = {_key(root, e.path): e for e in existing.repos} if existing else {}
    picked: dict[str, Any] = {}
    for repo in selected:
        key = _key(root, repo.path)
        before = previous.get(key)
        picked[key] = RepoEntry(
            path=repo.path.relative_to(root).as_posix(),
            alias=repo.alias,
            tags=list(before.tags) if before else [],
        )

    if existing is None:
        return _finish(WorkspaceConfig(version=1), list(picked.values()), primary_alias)

    picked_aliases = {e.alias for e in picked.values()}
    left_out = [e for k, e in previous.items() if k not in picked]
    # A folder that no longer exists has nothing to keep.
    gone = [e for e in left_out if not (root / e.path).is_dir()]
    left_out = [e for e in left_out if e not in gone]
    replace = bool(left_out) and ask and _confirm_replace(console, left_out, len(picked))
    # Shortcut: a kept repo whose alias a picked repo now uses is dropped
    # rather than renamed, since other tools address repos by alias. The line
    # below names it; renaming would need the user to pick a new alias.
    kept = [] if replace else [e for e in left_out if e.alias not in picked_aliases]
    clashed = [] if replace else [e for e in left_out if e.alias in picked_aliases]
    _report_kept(console, kept, clashed, gone)

    keep_keys = {_key(root, e.path) for e in kept}
    repos = [picked.get(k, e) for k, e in previous.items() if k in picked or k in keep_keys]
    repos += [e for k, e in picked.items() if k not in previous]

    primary = primary_alias
    kept_aliases = {e.alias for e in kept} | {
        e.alias for k, e in previous.items() if k in picked
    }
    if not replace and not primary_picked and existing.default_repo in kept_aliases:
        primary = existing.default_repo
    return _finish(existing, repos, primary)


def _finish(config: Any, repos: list[Any], primary: str) -> Any:
    for entry in repos:
        entry.is_primary = entry.alias == primary
    config.repos = repos
    config.default_repo = primary
    return config


def _load_existing(root: Path, console: Console) -> Any | None:
    from repowise.core.workspace import WORKSPACE_CONFIG_FILENAME, WorkspaceConfig

    if not (root / WORKSPACE_CONFIG_FILENAME).is_file():
        return None
    try:
        return WorkspaceConfig.load(root)
    except Exception as exc:
        # An unreadable file has nothing to merge; the write replaces it.
        console.print(
            f"  [{WARN}]Could not read the existing workspace config "
            f"({escape(str(exc))}); replacing it.[/]"
        )
        return None


def _key(root: Path, path: str | Path) -> str:
    return os.path.normcase(str((root / path).resolve()))


def _names(entries: list[Any]) -> str:
    names = [escape(e.alias) for e in entries[:_NAMES_SHOWN]]
    more = len(entries) - _NAMES_SHOWN
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


def _confirm_replace(console: Console, left_out: list[Any], picked: int) -> bool:
    console.print(
        f"  [{WARN}]The workspace already lists {plural(len(left_out), 'repo')} "
        f"you did not pick: {_names(left_out)}[/]"
    )
    return click.confirm(
        f"  Replace the workspace with only the {plural(picked, 'repo')} you picked?",
        default=False,
    )


def _report_kept(console: Console, kept: list[Any], clashed: list[Any], gone: list[Any]) -> None:
    if kept:
        console.print(
            f"  [{OK}]✓[/] Kept {plural(len(kept), 'existing repo')} in the workspace "
            f"({_names(kept)}). Remove one with `repowise workspace remove <alias>`."
        )
    if clashed:
        console.print(
            f"  [{WARN}]Dropped {_names(clashed)}: a repo you picked now uses that alias.[/]"
        )
    if gone:
        console.print(f"  [{WARN}]Dropped {_names(gone)}: the folder no longer exists.[/]")
