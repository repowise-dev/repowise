"""Command target resolution: single repo or workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import click
from rich.console import Console

from repowise.cli._helpers.paths import (
    STATE_FILENAME,
    find_workspace_root,
    get_repowise_dir,
    resolve_repo_path,
)

# ---------------------------------------------------------------------------
# Command target resolution — auto-detect single-repo vs workspace mode
# ---------------------------------------------------------------------------
#
# Many CLI commands (``update``, ``status``, ``watch``, ``generate-claude-md``,
# ``doctor``, ``costs``, ``search``, ``dead-code``, ``decision``, hooks) need
# to decide whether the user means "this one repo" or "the surrounding
# workspace". Historically each command did its own ad-hoc detection (or
# none), which produced the Phase A bug where ``repowise update`` from a
# workspace root errored with a misleading "No previous sync found" message
# and left a stray ``.repowise/`` directory behind.
#
# ``resolve_command_target`` is the single source of truth. Every command
# should call it before doing any work. See ``docs/WORKSPACE_ROBUSTNESS.md``
# for the UX principles.


@dataclass
class CommandTarget:
    """Resolved target for a CLI invocation — single repo or workspace.

    Attributes:
        mode: ``"single"`` or ``"workspace"``.
        repo_path: For single mode, the resolved repo path. For workspace
            mode, ``None`` (use ``ws_root`` + ``ws_config`` instead, or the
            ``primary_path()`` helper).
        ws_root: Workspace root path. Set in workspace mode; also set in
            single mode when a workspace exists *upstream* of the chosen
            repo, so commands can surface that context.
        ws_config: Loaded workspace config (workspace mode only).
        repo_filter: Optional alias filter for workspace mode (e.g.
            ``--repo backend``). ``None`` means "all repos".
        reason: Short human-readable explanation of why this target was
            chosen. Surfaced via :meth:`notice`.
        auto_detected: ``True`` when the workspace context was inferred
            rather than requested via an explicit flag. Used to decide
            whether to print a transparency notice.
    """

    mode: Literal["single", "workspace"]
    repo_path: Path | None = None
    ws_root: Path | None = None
    ws_config: Any | None = None  # WorkspaceConfig (avoid hard import here)
    repo_filter: str | None = None
    reason: str = ""
    auto_detected: bool = False

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def is_workspace(self) -> bool:
        return self.mode == "workspace"

    def primary_path(self) -> Path | None:
        """Return the workspace's primary repo path, if known."""
        if self.ws_config is None or self.ws_root is None:
            return None
        primary = self.ws_config.get_primary()
        if primary is None:
            return None
        return (self.ws_root / primary.path).resolve()

    def resolve_repo_alias(self, alias: str | None) -> Path | None:
        """Resolve an alias to an absolute repo path within the workspace.

        Returns ``None`` if the workspace is not loaded or the alias is
        unknown. Used by commands that accept ``--repo <alias>``.
        """
        if self.ws_config is None or self.ws_root is None or alias is None:
            return None
        entry = self.ws_config.get_repo(alias)
        if entry is None:
            return None
        return (self.ws_root / entry.path).resolve()

    def single_repo_path(self) -> Path:
        """The one repository to read, narrowing workspace mode to a repo.

        ``--repo <alias>`` resolves to ``mode="workspace"`` with ``repo_path``
        left ``None``, so a command that reads ``repo_path`` directly refuses
        every ``--repo`` call it advertises. A command that also prints its
        scope calls :meth:`narrow` instead.

        Raises ``click.ClickException`` when the alias is unknown or the
        workspace declares no primary.
        """
        if not self.is_workspace:
            assert self.repo_path is not None
            return self.repo_path
        if self.repo_filter is not None:
            picked = self.resolve_repo_alias(self.repo_filter)
            if picked is None:
                raise click.ClickException(f"Unknown repo alias: {self.repo_filter}")
            return picked
        primary = self.primary_path()
        if primary is None:
            raise click.ClickException("Workspace has no primary repo configured.")
        return primary

    def narrow(self, console_obj: Console, *, command: str, repo_option: bool = True) -> Path:
        """:meth:`single_repo_path`, after one line saying which repo the command reads.

        For commands that read one repo even from a workspace root, where
        :meth:`notice` would claim the whole workspace. *repo_option* says
        whether the command takes ``--repo <alias>``, so the hint names a way
        to pick another repo that the command offers.
        """
        from rich.markup import escape

        repo = self.single_repo_path()
        if self.is_workspace:
            assert self.ws_root is not None and self.ws_config is not None
            role, hint = "", ""
            alias = self.repo_filter
            if alias is None:
                alias, role = self.ws_config.get_primary().alias, " (primary)"
                hint = (
                    "; pass --repo <alias> for another"
                    if repo_option
                    else "; run it inside another member repo for that one"
                )
            console_obj.print(
                f"[dim]\\[workspace][/dim] {command} reads one repo: "
                f"[cyan]{escape(alias)}[/cyan]{role} of {len(self.ws_config.repos)} "
                f"in [cyan]{escape(self.ws_root.name)}[/cyan]{hint}"
            )
        elif self.auto_detected and self.ws_root is not None:
            console_obj.print(
                f"[dim]\\[single-repo][/dim] {command} reads [cyan]{escape(repo.name)}[/cyan], "
                f"a repo inside workspace [cyan]{escape(self.ws_root.name)}[/cyan]"
            )
        return repo

    # ------------------------------------------------------------------
    # Notice rendering — every command should call this so users always
    # know which mode they ended up in.
    # ------------------------------------------------------------------

    def notice(self, console_obj: Console, *, command: str = "") -> None:
        """Print a one-line transparency notice describing the chosen target.

        - Always printed when ``auto_detected`` is True.
        - Also printed when in workspace mode (even if flagged explicitly)
          so the repo list is visible at the top of the command output.
        - Silent when single-repo mode was explicitly requested.
        """
        if self.mode == "workspace":
            ws_root = self.ws_root.name if self.ws_root else "?"
            repos = len(self.ws_config.repos) if self.ws_config else 0
            if self.repo_filter:
                console_obj.print(
                    f"[dim]\\[workspace][/dim] {command or 'running'} on "
                    f"[cyan]{self.repo_filter}[/cyan] within "
                    f"[cyan]{ws_root}[/cyan] ({repos} repos)"
                )
            else:
                console_obj.print(
                    f"[dim]\\[workspace][/dim] {command or 'running'} across "
                    f"[cyan]{repos}[/cyan] repos in [cyan]{ws_root}[/cyan]"
                )
            if self.reason and self.auto_detected:
                console_obj.print(f"[dim]  ({self.reason})[/dim]")
            return

        # Single-repo mode — only narrate when the resolution was non-obvious.
        if self.auto_detected and self.ws_root is not None:
            # A workspace exists upstream but we chose single-repo anyway.
            console_obj.print(
                f"[dim]\\[single-repo][/dim] targeting "
                f"[cyan]{self.repo_path}[/cyan] "
                f"(workspace also detected at [cyan]{self.ws_root}[/cyan]; "
                f"pass --workspace to run across all repos)"
            )


class WorkspaceNotFound(click.ClickException):
    """Raised when ``--workspace`` was requested but no workspace was found."""


def resolve_command_target(
    *,
    path: str | None = None,
    workspace_flag: bool = False,
    no_workspace_flag: bool = False,
    repo_alias: str | None = None,
) -> CommandTarget:
    """Resolve whether a command should operate on a single repo or workspace.

    The resolution rules (first match wins):

    1. ``--no-workspace`` → single-repo targeting ``path`` (or cwd). Hard
       override for users who want the old behavior.
    2. ``--workspace`` or ``--repo <alias>`` → workspace mode. Raises
       :class:`WorkspaceNotFound` if no workspace can be located.
    3. Explicit ``path`` argument:
       - If the path itself contains ``.repowise-workspace.yaml`` →
         workspace mode (treats the path as the workspace root).
       - Otherwise → single-repo mode targeting that path. We do *not*
         auto-promote to workspace when the user has explicitly typed a
         path — explicit beats implicit.
    4. No ``path``, no flags → start from cwd and:
       - If cwd is itself a workspace root → workspace mode.
       - If cwd has its own ``.repowise/state.json`` (i.e. it's a repo
         that has been indexed before) → single-repo mode, even if a
         workspace exists upstream. cd-into-the-repo is the strongest
         signal of user intent.
       - If a workspace exists upstream of cwd → workspace mode.
       - Otherwise → single-repo mode (cwd, even if not indexed).

    The returned :class:`CommandTarget` carries a ``reason`` string and an
    ``auto_detected`` flag so commands can render a transparent notice.
    """
    if workspace_flag and no_workspace_flag:
        raise click.UsageError("--workspace and --no-workspace are mutually exclusive.")

    if repo_alias is not None and no_workspace_flag:
        raise click.UsageError(
            "--repo <alias> implies workspace mode, but --no-workspace was passed."
        )

    explicit_path = path is not None
    base_path = resolve_repo_path(path)

    # Local import — avoids a circular import (core.workspace pulls in providers
    # which pull in CLI helpers in some edge cases).
    from repowise.core.workspace.config import (
        WORKSPACE_CONFIG_FILENAME,
        WorkspaceConfig,
    )

    def _load_ws(root: Path) -> Any | None:
        try:
            return WorkspaceConfig.load(root)
        except Exception:
            return None

    # ----- Rule 1: explicit --no-workspace -----
    if no_workspace_flag:
        return CommandTarget(
            mode="single",
            repo_path=base_path,
            reason="forced via --no-workspace",
            auto_detected=False,
        )

    # ----- Rule 2: --workspace or --repo -----
    if workspace_flag or repo_alias is not None:
        ws_root = find_workspace_root(base_path)
        if ws_root is None:
            raise WorkspaceNotFound(
                "No .repowise-workspace.yaml found at or above "
                f"{base_path}. Run 'repowise init <workspace-dir>' to "
                "create a workspace, or drop the --workspace flag."
            )
        ws_config = _load_ws(ws_root)
        if ws_config is None:
            raise WorkspaceNotFound(
                f"Found workspace config at {ws_root} but couldn't load it. Is it valid YAML?"
            )
        if repo_alias is not None and ws_config.get_repo(repo_alias) is None:
            available = ", ".join(ws_config.repo_aliases()) or "(none)"
            raise click.UsageError(
                f"Unknown repo alias '{repo_alias}' in workspace. Available: {available}"
            )
        reason = "via --workspace flag" if workspace_flag else f"via --repo {repo_alias}"
        return CommandTarget(
            mode="workspace",
            ws_root=ws_root,
            ws_config=ws_config,
            repo_filter=repo_alias,
            reason=reason,
            auto_detected=False,
        )

    # ----- Rule 3: explicit path argument -----
    if explicit_path:
        # Is the path itself a workspace root?
        if (base_path / WORKSPACE_CONFIG_FILENAME).is_file():
            ws_config = _load_ws(base_path)
            if ws_config is not None:
                return CommandTarget(
                    mode="workspace",
                    ws_root=base_path,
                    ws_config=ws_config,
                    reason="path argument is a workspace root",
                    auto_detected=True,
                )
        # Otherwise treat as single-repo. Surface workspace context if any.
        upstream = find_workspace_root(base_path)
        return CommandTarget(
            mode="single",
            repo_path=base_path,
            ws_root=upstream,
            reason="explicit path argument",
            auto_detected=False,
        )

    # ----- Rule 4: no path, no flags -----
    # 4a: cwd is itself a workspace root
    if (base_path / WORKSPACE_CONFIG_FILENAME).is_file():
        ws_config = _load_ws(base_path)
        if ws_config is not None:
            return CommandTarget(
                mode="workspace",
                ws_root=base_path,
                ws_config=ws_config,
                reason="cwd is the workspace root",
                auto_detected=True,
            )

    # 4b: cwd is an indexed repo — respect that even if a workspace exists upstream
    cwd_state = get_repowise_dir(base_path) / STATE_FILENAME
    if cwd_state.exists():
        upstream = find_workspace_root(base_path.parent if base_path.parent != base_path else None)
        return CommandTarget(
            mode="single",
            repo_path=base_path,
            ws_root=upstream,
            reason="cwd has its own .repowise/state.json (cd-into-repo wins)",
            auto_detected=upstream is not None,
        )

    # 4c: workspace exists upstream of cwd → workspace mode
    upstream = find_workspace_root(base_path)
    if upstream is not None:
        ws_config = _load_ws(upstream)
        if ws_config is not None:
            return CommandTarget(
                mode="workspace",
                ws_root=upstream,
                ws_config=ws_config,
                reason=f"workspace detected upstream at {upstream}",
                auto_detected=True,
            )

    # 4d: plain single-repo mode (likely uninitialized).
    return CommandTarget(
        mode="single",
        repo_path=base_path,
        reason="no workspace nearby",
        auto_detected=False,
    )


def cross_repo_cut_inputs(repo_path: Path, ws_root: Path | None) -> dict[str, Any]:
    """``lookup`` and ``alias`` for the dead-code cross-repo cut (``serve_findings``)
    on *repo_path*, a member of the workspace at *ws_root*; both ``None`` when
    there is no workspace or the repo is not one of its members."""
    none: dict[str, Any] = {"lookup": None, "alias": None}
    if ws_root is None:
        return none
    import yaml

    from repowise.core.workspace.config import WorkspaceConfig

    try:
        alias = WorkspaceConfig.load(ws_root).alias_for_path(ws_root, repo_path)
    except (OSError, ValueError, yaml.YAMLError):  # an unreadable config cuts nothing
        return none
    if alias is None:
        return none
    from repowise.server.mcp_server._enrichment import CrossRepoEnricher

    return {"lookup": CrossRepoEnricher.for_workspace(ws_root), "alias": alias}
