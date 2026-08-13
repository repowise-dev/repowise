"""Executing a plan, and reporting what actually happened.

Composes the six targets' own ``uninstall()`` rather than reimplementing any
removal. Two implementations of the same removal, drifting apart, is the shape
of every expensive bug on this track, and a command whose whole job is removing
more would be the worst place to add a second one.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from repowise.cli.agent_targets.types import FileAction

from .inventory import Group, Item, Plan

#: Ran fine, everything chosen is gone.
EXIT_CLEAN = 0
#: A removal failed.
EXIT_FAILED = 1
#: Ran fine, and something the user chose is still there. Not an error: it is
#: the honest answer to "is it all gone", and folding it into zero would make
#: the trust claim unverifiable from a script.
#:
#: Three rather than the obvious two, because Click already spends 2 on
#: ``UsageError``. Sharing it would leave a script unable to tell "I removed
#: what I could and something remains" from "you typed the command wrong",
#: which is precisely the distinction these codes exist to carry.
EXIT_LEFTOVERS = 3
#: Nothing was removed because no scope was named and nobody could be asked.
EXIT_NEEDS_SCOPE = 4


@dataclass(frozen=True)
class Result:
    """What happened to one path."""

    group: Group
    path: Path
    action: FileAction
    label: str
    reason: str | None = None

    def as_dict(self) -> dict:
        return {
            "group": self.group.value,
            "path": str(self.path),
            "action": self.action.value,
            "label": self.label,
            "reason": self.reason,
        }


@dataclass
class Outcome:
    results: list[Result] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def leftovers(self) -> list[Result]:
        """Everything the user asked to remove that is still there."""
        return [r for r in self.results if r.action in (FileAction.KEPT, FileAction.FAILED)]

    @property
    def exit_code(self) -> int:
        if any(r.action is FileAction.FAILED for r in self.results):
            return EXIT_FAILED
        if any(r.action is FileAction.KEPT for r in self.results):
            return EXIT_LEFTOVERS
        return EXIT_CLEAN

    @property
    def complete(self) -> bool:
        return not self.leftovers

    def as_dict(self) -> dict:
        return {
            "results": [r.as_dict() for r in self.results],
            "notes": list(self.notes),
            "complete": self.complete,
            "leftovers": [r.as_dict() for r in self.leftovers],
        }


def _remove_agents(repo_path: Path, plan: Plan) -> tuple[list[Result], list[str]]:
    """Run every wired target's own uninstall, in one batch.

    The batch matters. ``registry.removing`` tells the shared-file guard which
    targets are on their way out, so three agents managing ``AGENTS.md`` do not
    each keep the block for the other two and leave it behind. Removing them one
    command at a time genuinely does leave the block, which is a documented
    limitation of the guard rather than something to work around here.
    """
    from repowise.cli.agent_targets.registry import all_targets, get_target, removing
    from repowise.cli.agent_targets.types import Scope

    wired: dict[str, set[Scope]] = {}
    for item in plan.for_groups(frozenset({Group.AGENTS})):
        if item.target_id is None or item.scope is None:
            continue
        wired.setdefault(item.target_id, set()).add(Scope(item.scope))

    results: list[Result] = []
    notes: list[str] = []
    if not wired:
        return results, notes

    ordered = [target.id for target in all_targets() if target.id in wired]
    with removing(ordered):
        for target_id in ordered:
            target = get_target(target_id)
            for scope in (Scope.PROJECT, Scope.USER):
                if scope not in wired[target_id]:
                    continue
                try:
                    write = target.uninstall(scope, repo_path=repo_path)
                except Exception as exc:
                    # One target that raises must not abort the other five, and
                    # must not be reported as a removal that happened. `init`
                    # learned this the expensive way: an unguarded call aborted
                    # a run part-way through with other agents already written.
                    results.append(
                        Result(
                            group=Group.AGENTS,
                            path=repo_path,
                            action=FileAction.FAILED,
                            label=f"{target.display_name} ({scope.value})",
                            reason=str(exc),
                        )
                    )
                    continue
                notes.extend(write.notes)
                for written in write.files:
                    results.append(
                        Result(
                            group=Group.AGENTS,
                            path=written.path,
                            action=written.action,
                            label=f"{target.display_name} ({scope.value})",
                            reason=written.reason,
                        )
                    )
    return results, notes


def _remove_repo_files(repo_path: Path, plan: Plan) -> list[Result]:
    from .generated_files import generated_blocks, remove_block

    by_path = {item.path: item for item in plan.for_groups(frozenset({Group.REPO_FILES}))}
    results: list[Result] = []
    for block in generated_blocks():
        item = by_path.get(block.path(repo_path))
        label = item.label if item else block.marker_tag
        if item is not None and item.blocked:
            results.append(
                Result(
                    group=Group.REPO_FILES,
                    path=item.path,
                    action=FileAction.KEPT,
                    label=label,
                    reason=item.blocked,
                )
            )
            continue
        path, action, reason = remove_block(block, repo_path)
        results.append(
            Result(group=Group.REPO_FILES, path=path, action=action, label=label, reason=reason)
        )
    return results


def _is_link(path: Path) -> bool:
    """Symlink or Windows junction.

    ``is_symlink()`` alone is not enough and this is the platform we ship on:
    a junction, which is how a Windows user relocates a directory, reports
    ``is_symlink() == False``. ``rmtree`` happens to refuse both, so the guard
    was not the thing keeping the target safe; it was only deciding whether the
    row said "refused because it is a link" or "failed" with a message about
    symbolic links that named neither the junction nor what to do.
    """
    import os

    if path.is_symlink():
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is None:
        return False
    try:
        return bool(isjunction(path))
    except OSError:
        return False


def _remove_tree(item: Item) -> Result:
    """Delete a directory whose name is ours, and refuse anything else.

    Ownership is the path, not the contents. ``.repowise/`` and ``~/.repowise/``
    are named by us and created by us, so removing them needs no inspection of
    what is inside and therefore has no invariant to get wrong. That is the only
    tier of ownership on which this command deletes a whole tree.

    A symlink is refused rather than followed. Deleting through a junction
    reaches somewhere this command never enumerated and never showed the user.
    """
    if item.blocked:
        return Result(
            group=item.group,
            path=item.path,
            action=FileAction.KEPT,
            label=item.label,
            reason=item.blocked,
        )
    if not item.path.exists():
        return Result(
            group=item.group, path=item.path, action=FileAction.NOT_FOUND, label=item.label
        )
    if _is_link(item.path):
        return Result(
            group=item.group,
            path=item.path,
            action=FileAction.KEPT,
            label=item.label,
            reason="this is a symlink or junction, and following it would delete something "
            "outside the tree this command enumerated",
        )
    try:
        shutil.rmtree(item.path)
    except OSError as exc:
        return Result(
            group=item.group,
            path=item.path,
            action=FileAction.FAILED,
            label=item.label,
            reason=str(exc),
        )
    # Proven from disk rather than from the absence of an exception. `rmtree`
    # can return having left files behind on Windows when something holds a
    # handle, and reporting REMOVED over a directory that is still there is the
    # false success this track has already shipped once.
    if item.path.exists():
        return Result(
            group=item.group,
            path=item.path,
            action=FileAction.FAILED,
            label=item.label,
            reason="the directory was still present after the delete",
        )
    return Result(group=item.group, path=item.path, action=FileAction.REMOVED, label=item.label)


def execute(plan: Plan, groups: frozenset[Group]) -> Outcome:
    """Carry out *plan*, restricted to *groups*."""
    outcome = Outcome()

    if Group.AGENTS in groups:
        results, notes = _remove_agents(plan.repo_path, plan)
        outcome.results.extend(results)
        outcome.notes.extend(notes)

    if Group.REPO_FILES in groups:
        outcome.results.extend(_remove_repo_files(plan.repo_path, plan))

    # After the agent and repo-file passes, never before: both write into the
    # repo, and `.repowise/` holds the config that `describe_paths` consults.
    for group in (Group.INDEX, Group.GLOBAL):
        if group not in groups:
            continue
        for item in plan.for_groups(frozenset({group})):
            outcome.results.append(_remove_tree(item))

    if Group.GLOBAL in groups and any(
        r.group is Group.GLOBAL and r.action is FileAction.REMOVED for r in outcome.results
    ):
        outcome.notes.append(
            "Machine-wide state is gone, including your telemetry preference and your "
            "anonymous id. A future install will ask about telemetry again."
        )
    return outcome
