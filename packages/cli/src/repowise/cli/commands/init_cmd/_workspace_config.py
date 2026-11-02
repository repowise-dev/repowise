"""The workspace config a workspace init writes, merged with the one already there.

Picking a subset of repos on a re-run must not silently shrink the workspace:
repos the user did not pick stay listed unless an interactive user asks to
replace the list, and the existing primary stays unless the user picked one.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.markup import escape

from repowise.cli.ui import OK, WARN
from repowise.cli.ui.glyphs import glyph
from repowise.core.ci.markdown import plural

_NAMES_SHOWN = 5


@dataclass(frozen=True)
class WorkspacePick:
    """What the user picked: the repos to index and the primary among them."""

    selected: list[Any]
    root: Path
    primary_alias: str
    primary_repo: Any
    #: The user chose the primary in a prompt, so it replaces the existing one.
    primary_picked: bool


def build_workspace_config(pick: WorkspacePick, *, console: Console, ask: bool) -> Any:
    """The config for the picked repos, keeping what an existing config lists.

    *ask* allows a prompt before repos the user did not pick are dropped;
    without it they are always kept.
    """
    from repowise.core.workspace import WorkspaceConfig

    root = pick.root
    existing = _load_existing(root, console)
    previous = {_key(root, e.path): e for e in existing.repos} if existing else {}
    picked = _picked_entries(pick, previous)
    if existing is None:
        return _finish(WorkspaceConfig(version=1), list(picked.values()), pick.primary_alias)

    left_out = [e for k, e in previous.items() if k not in picked]
    replace, kept, clashed = _settle_left_out(root, left_out, picked, console=console, ask=ask)
    repos = _merged_repos(root, previous, picked, kept)
    keep_primary = not replace and not pick.primary_picked
    primary = _primary(existing.default_repo, repos, clashed, pick.primary_alias, keep_primary)
    return _finish(existing, repos, primary)


def _picked_entries(pick: WorkspacePick, previous: dict[str, Any]) -> dict[str, Any]:
    """One entry per picked repo, keyed by real path, carrying its existing tags."""
    from repowise.core.workspace import RepoEntry

    picked: dict[str, Any] = {}
    for repo in pick.selected:
        key = _key(pick.root, repo.path)
        before = previous.get(key)
        picked[key] = RepoEntry(
            path=repo.path.relative_to(pick.root).as_posix(),
            alias=repo.alias,
            tags=list(before.tags) if before else [],
        )
    return picked


def _merged_repos(
    root: Path, previous: dict[str, Any], picked: dict[str, Any], kept: list[Any]
) -> list[Any]:
    """Existing order first (picked entries replace theirs in place), then new picks."""
    keep_keys = {_key(root, e.path) for e in kept} | picked.keys()
    repos = [picked.get(k, e) for k, e in previous.items() if k in keep_keys]
    return repos + [e for k, e in picked.items() if k not in previous]


def _primary(
    old: str | None, repos: list[Any], clashed: list[Any], picked: str, keep_old: bool
) -> str:
    # A clashed alias now names a different repo, so it is not the old primary.
    still_there = {e.alias for e in repos} - {e.alias for e in clashed}
    return old if keep_old and old in still_there else picked


def _settle_left_out(
    root: Path, left_out: list[Any], picked: dict[str, Any], *, console: Console, ask: bool
) -> tuple[bool, list[Any], list[Any]]:
    """Whether to replace the workspace, and the left-out repos kept and clashed."""
    # A folder that no longer exists has nothing to keep.
    gone, present = _partition(left_out, lambda e: not (root / e.path).is_dir())
    replace = bool(present) and ask and _confirm_replace(console, present, len(picked))
    picked_aliases = {e.alias for e in picked.values()}
    # Shortcut: a kept repo whose alias a picked repo now uses is dropped
    # rather than renamed, since other tools address repos by alias. The line
    # below names it; renaming would need the user to pick a new alias.
    clashed, kept = ([], []) if replace else _partition(present, lambda e: e.alias in picked_aliases)
    _report_kept(console, kept, clashed, gone)
    return replace, kept, clashed


def _partition(items: list[Any], pred: Callable[[Any], bool]) -> tuple[list[Any], list[Any]]:
    """(items matching *pred*, the rest), each in order."""
    yes = [e for e in items if pred(e)]
    return yes, [e for e in items if not pred(e)]


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
    # A malformed file raises from YAML, or from the shape checks in from_dict.
    except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
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
            f"  [{OK}]{glyph('ok')}[/] Kept {plural(len(kept), 'existing repo')} in the workspace "
            f"({_names(kept)}). Remove one with `repowise workspace remove <alias>`."
        )
    if clashed:
        console.print(
            f"  [{WARN}]Dropped {_names(clashed)}: a repo you picked now uses that alias.[/]"
        )
    if gone:
        console.print(f"  [{WARN}]Dropped {_names(gone)}: the folder no longer exists.[/]")
