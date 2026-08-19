"""The full inventory of what repowise has written, computed without writing.

This module is the trust claim. The deletion is not: a command that removes
everything and says nothing is less honest than one that removes half and names
the other half. So the plan enumerates every path in every group, present or
not, and carries for each either the action a run would take or the reason it
would not. ``--dry-run`` prints this and stops; a real run executes exactly it.

Nothing here opens a file for writing or creates a directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Group(StrEnum):
    """The four things a user can independently choose to remove.

    Deliberately not the two scopes the ``agents`` command uses. Project and
    user are *locations*; these are *kinds*, and they cross-cut. Agent wiring
    exists at both locations and a user removing it means both.
    """

    #: All six agent targets, both scopes, through their own ``uninstall()``.
    AGENTS = "agents"
    #: The managed blocks the generators write into the repo's own files.
    REPO_FILES = "repo-files"
    #: The repo's ``.repowise/`` directory.
    INDEX = "index"
    #: ``~/.repowise/``, which is machine-wide.
    GLOBAL = "global"


#: Ticked when the user just presses enter.
#:
#: The index is out, and so is machine-wide state, for two different reasons.
#:
#: The index is out because the cost is asymmetric in one direction. Someone
#: leaving who misses the box loses a directory the report just named; someone
#: reinstalling who misses it loses a full reindex. One keystroke either way.
#:
#: Machine-wide state is out because ``~/.repowise/`` holds no registry of the
#: repos on this machine, so a command running inside one repo cannot see the
#: other repos it would break by deleting a login or the shared embedder key.
#: ``--all`` ticks both, because that is the user naming them.
DEFAULT_GROUPS = frozenset({Group.AGENTS, Group.REPO_FILES})

ALL_GROUPS = frozenset(Group)


@dataclass(frozen=True)
class Item:
    """One path, and what would happen to it.

    *blocked* is the field that keeps the output honest. A path we will not
    touch is still listed, with the reason on the row, because a trust command
    that silently omits what it declined has told the user nothing about it.
    """

    group: Group
    path: Path
    #: Short label for the row, e.g. "Claude Code (user)" or "index".
    label: str
    exists: bool
    #: Bytes on disk where cheap to compute, else ``None``.
    size: int | None = None
    #: Why this path will not be touched, or ``None`` when it will be.
    blocked: str | None = None
    #: For the agents group: the target id whose ``uninstall`` owns this path.
    target_id: str | None = None
    #: For the agents group: ``"project"`` or ``"user"``. Carried as data
    #: because the runner needs it, and recovering it by matching on the end of
    #: the display label meant any wording change silently reclassified every
    #: user-scope path as project scope.
    scope: str | None = None

    def as_dict(self) -> dict:
        return {
            "group": self.group.value,
            "path": str(self.path),
            "label": self.label,
            "exists": self.exists,
            "size": self.size,
            "blocked": self.blocked,
            "target_id": self.target_id,
            "scope": self.scope,
        }


@dataclass
class Plan:
    """Everything the command knows before it does anything."""

    repo_path: Path
    items: list[Item] = field(default_factory=list)
    #: Lines that are true regardless of what the user picks: the package, the
    #: plugin, hand-pasted config, a detected workspace.
    advisories: list[str] = field(default_factory=list)

    def for_groups(self, groups: frozenset[Group]) -> list[Item]:
        return [item for item in self.items if item.group in groups]

    def groups_present(self) -> list[Group]:
        """Groups with something to report, in declaration order.

        ``blocked`` counts as something to report even when ``exists`` is False.
        A generated block with an orphaned marker pair is exactly that shape,
        and keying on ``exists`` alone dropped its whole group out of the
        checklist, so the one case that most needs the user's attention was the
        one the prompt did not mention.
        """
        found = {item.group for item in self.items if item.exists or item.blocked}
        return [group for group in Group if group in found]

    def as_dict(self) -> dict:
        return {
            "repo": str(self.repo_path),
            "items": [item.as_dict() for item in self.items],
            "advisories": list(self.advisories),
        }


def _size_of(path: Path) -> int | None:
    """Bytes at *path*, walking a directory. ``None`` when it cannot be read.

    Best-effort by design: this number decorates a row, and a permission error
    partway through a tree must not stop the command that is about to offer to
    delete it.

    Through ``fs_walk.iter_glob`` rather than ``Path.rglob``, which the walk
    guard in ``tests/unit/ingestion/test_fs_walk.py`` insists on and which this
    code earns twice over: a bare ``rglob`` follows junction cycles, and this
    walks two directories a user is free to have relocated. The pruning can
    under-count a size by skipping a ``node_modules``-shaped name inside our own
    directory, which is a rounding error on a figure that decorates a row.
    """
    from repowise.core.fs_walk import iter_glob

    try:
        if path.is_file():
            return path.stat().st_size
        if not path.is_dir():
            return None
        total = 0
        for entry in iter_glob(path, "*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
        return total
    except OSError:
        return None


def _index_blocked(repo_path: Path) -> str | None:
    """Why the index cannot be deleted right now, or ``None``.

    Two live-process checks, both cheap, both about deleting a directory out
    from under something that is currently reading and writing it.
    """
    from repowise.cli.helpers import get_repowise_dir

    try:
        from repowise.core.update_lock import read_update_lock

        if read_update_lock(repo_path) is not None:
            return "an update is running for this repo; wait for it to finish"
    except Exception:
        # A lock helper that raises must not stop the inventory from being
        # printed. Not knowing whether an update holds the lock is a worse
        # answer than "no", but printing nothing at all is worse than both.
        pass

    lock = get_repowise_dir(repo_path) / "serve.lock.json"
    if _serve_lock_is_live(lock):
        return "'repowise serve' is running for this repo; stop it first"
    return None


def _serve_lock_is_live(lock: Path) -> bool:
    """Whether ``serve.lock.json`` names a process that still exists.

    A stale lock is the common case: ``serve`` only removes a lock it owns, so
    a killed server leaves one behind forever. Treating a stale lock as live
    would make the index permanently undeletable, which is a worse failure than
    the one this guard prevents.
    """
    import json

    if not lock.exists():
        return False
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pid = payload.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        from repowise.core.procutils import pid_alive

        return bool(pid_alive(pid))
    except Exception:
        # Without a liveness probe, assume it is stale rather than blocking the
        # user out of their own directory on the strength of a leftover file.
        return False


#: Read in chunks rather than whole, with an overlap so the marker cannot be
#: split across a boundary. A size cap that skipped large files outright would
#: reintroduce the very miss this function exists to close, on the one input we
#: cannot bound: a user's own ``settings.json`` or ``AGENTS.md``.
_PROBE_CHUNK = 1 << 20
_MARKER = b"repowise"

#: How many entries of a described directory to look at. One of the eighteen
#: described paths is a directory and it holds eighteen small files.
_PROBE_DIR_ENTRIES = 200


def _mentions_repowise(path: Path) -> bool:
    """Whether *path* names us, read as bytes.

    Bytes rather than text, and a substring rather than a parse. The whole
    reason this exists is the file the parser could not read, so anything that
    goes through a parser reintroduces the hole it is closing.

    The asymmetry is deliberate. A false positive costs one row that reports
    not-found and one target's uninstall running with nothing to remove. A false
    negative leaves a live registration behind and says nothing, which is the
    failure this command exists to prevent.

    **Directories are searched, not skipped.** Exactly one of the eighteen paths
    the six targets describe is a directory, ``~/.codex/prompts``, and it is the
    only evidence that Codex user scope is wired when the hooks file is JSONC or
    absent. Answering False for it left eighteen prompt files on disk under
    "everything selected is gone".
    """
    try:
        if path.is_dir():
            # Unsorted, so a directory with more entries than the cap does not
            # depend on where `repowise-` falls alphabetically. `~/.codex/prompts`
            # is documented as a flat global directory shared with every other
            # tool the user has installed, so it can be large.
            #
            # `os.fsencode` rather than `str.encode`, which raises
            # `UnicodeEncodeError` on a filename holding lone surrogates. That is
            # a `ValueError`, not an `OSError`, so it would escape the handler
            # below and out of `build_plan`.
            for index, child in enumerate(path.iterdir()):
                if index >= _PROBE_DIR_ENTRIES:
                    return False
                if _MARKER in os.fsencode(child.name).lower():
                    return True
            return False
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            tail = b""
            while chunk := handle.read(_PROBE_CHUNK):
                if _MARKER in (tail + chunk).lower():
                    return True
                tail = chunk[-len(_MARKER) :]
        return False
    except OSError:
        return False


def _agent_items(repo_path: Path) -> list[Item]:
    """Every path the currently wired agents manage, per target and scope.

    Sourced from each target's own ``describe_paths`` rather than a list kept
    here, so a target that starts writing a fourth file appears in the plan
    without this module being edited.

    **A scope counts as wired if ``detect`` says so, or if one of its files
    mentions us.** Both halves are needed and each was learned by getting it
    wrong.

    Detection alone is not enough: a ``.vscode/mcp.json`` with a comment in it
    is JSONC, which VS Code accepts and our strict parse refuses, so ``detect``
    returned nothing, the file never entered the inventory, and the command
    removed a live MCP registration from nowhere while printing "everything
    selected is gone". Any target whose ``detect`` raises had the same hole.

    Existence alone is far worse. Only three of the eighteen paths the six
    targets describe are repowise-exclusive. The rest are files that exist for
    anyone who has ever run the host at all: ``~/.claude/settings.json``,
    ``~/.codex/hooks.json``, ``.vscode/extensions.json``, ``AGENTS.md``,
    ``.mcp.json``. Keying on existence listed every one of them in a repo where
    repowise had never been installed, pre-ticked the agent group, and had the
    runner call ``uninstall`` for scopes detection never found.

    So the fallback is a text probe rather than a stat: does a file we would
    touch actually name us. That is the same shape as OpenCode's own JSONC
    fallback, it cannot be fooled by a host the user merely happens to have, and
    it cannot miss a file that genuinely holds our entry.
    """
    from repowise.cli.agent_targets.registry import all_targets
    from repowise.cli.agent_targets.types import Scope

    items: list[Item] = []
    for target in all_targets():
        try:
            registrations = target.detect(repo_path)
        except Exception:
            registrations = []
        wired_scopes = {registration.scope for registration in registrations}
        for scope in (Scope.PROJECT, Scope.USER):
            if not target.supports_scope(scope):
                continue
            try:
                paths = [Path(raw) for raw in target.describe_paths(scope, repo_path=repo_path)]
            except Exception:
                continue
            if scope not in wired_scopes and not any(_mentions_repowise(path) for path in paths):
                continue
            for path in paths:
                items.append(
                    Item(
                        group=Group.AGENTS,
                        path=path,
                        scope=scope.value,
                        label=f"{target.display_name} ({scope.value})",
                        exists=path.exists(),
                        target_id=target.id,
                    )
                )
    return items


def _repo_file_items(repo_path: Path) -> list[Item]:
    from repowise.cli.agent_targets.formats.marker_block import BlockState, refusal_reason

    from .generated_files import generated_blocks, inspect_block

    items: list[Item] = []
    for block in generated_blocks():
        path = block.path(repo_path)
        state = inspect_block(block, repo_path)
        blocked = (
            refusal_reason(state)
            if state in (BlockState.ORPHANED, BlockState.DUPLICATED, BlockState.UNREADABLE)
            else None
        )
        items.append(
            Item(
                group=Group.REPO_FILES,
                path=path,
                label=f"{block.marker_tag} block",
                exists=state is BlockState.PRESENT,
                size=_size_of(path),
                blocked=blocked,
            )
        )
    return items


def _global_items() -> list[Item]:
    """``~/.repowise/`` as one row, with the two consequences named.

    One row rather than a file each: the directory is ours end to end, its name
    is ours, and deleting it needs no content inspection. What it does need is
    for the two costs to be visible before the box is ticked, which is what the
    label carries.
    """
    # Built from ``Path.home()`` rather than by calling ``user_global_dir()``,
    # which creates the directory as a side effect of resolving it. Taking the
    # inventory must not bring into existence the thing it is reporting on, and
    # under ``--dry-run`` that would be a write from a command that promised
    # none.
    try:
        root = Path.home() / ".repowise"
    except (OSError, RuntimeError):
        return []
    return [
        Item(
            group=Group.GLOBAL,
            path=root,
            label="machine-wide state (login, caches, telemetry preference)",
            exists=root.exists(),
            size=_size_of(root),
        )
    ]


def _advisories(repo_path: Path) -> list[str]:
    """Lines that are true whatever the user picks."""
    lines: list[str] = []

    lines.append(f"The package itself is not ours to remove. Run: {_package_uninstall_hint()}")

    from repowise.cli.agent_targets.targets.claude_code import (
        PLUGIN_KEY,
        plugin_manifest_path,
    )

    if plugin_manifest_path().exists():
        try:
            import json

            manifest = json.loads(plugin_manifest_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = None
        if manifest is not None and PLUGIN_KEY in str(manifest):
            lines.append(
                "The Claude Code plugin is host-owned and repowise cannot remove it. "
                f"In Claude Code, run: /plugin uninstall {PLUGIN_KEY}"
            )

    lines.append(
        "Hosts you configured by hand from 'repowise agents print-config' are not "
        "visible to us and were not touched."
    )

    from repowise.core.workspace.config import WORKSPACE_CONFIG_FILENAME

    if (repo_path / WORKSPACE_CONFIG_FILENAME).exists():
        lines.append(
            f"{WORKSPACE_CONFIG_FILENAME} is here, and this command is single-repo. "
            "Workspace-level files were not touched."
        )
    return lines


def _package_uninstall_hint() -> str:
    """The uninstall line for however repowise was actually installed.

    Detected rather than assumed. Telling a pipx user to run ``pip uninstall``
    hands them a command that reports success and removes nothing.
    """
    import sys

    executable = Path(sys.executable).resolve()
    parts = {part.lower() for part in executable.parts}
    if "pipx" in parts:
        return "pipx uninstall repowise"
    if "uv" in parts or (executable.parent / "uv.exe").exists() or (executable.parent / "uv").exists():
        return "uv tool uninstall repowise"
    return "pip uninstall repowise"


def build_plan(repo_path: Path) -> Plan:
    """Enumerate everything, touching nothing."""
    from repowise.cli.helpers import get_repowise_dir

    index_path = get_repowise_dir(repo_path)
    plan = Plan(repo_path=repo_path)
    plan.items.extend(_agent_items(repo_path))
    plan.items.extend(_repo_file_items(repo_path))
    plan.items.append(
        Item(
            group=Group.INDEX,
            path=index_path,
            label="index (rebuilding it costs a full re-index)",
            exists=index_path.exists(),
            size=_size_of(index_path),
            blocked=_index_blocked(repo_path) if index_path.exists() else None,
        )
    )
    plan.items.extend(_global_items())
    plan.advisories.extend(_advisories(repo_path))
    return plan
