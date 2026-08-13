"""AI editor setup orchestration for repowise init.

The indexing command should not know the details of each editor's config files,
global settings, or managed instruction files. This module keeps that product
setup layer behind an integration interface; concrete editor integrations live
in ``repowise.cli.editor_integrations`` and the config writes they drive live in
``repowise.cli.agent_targets``.

The interface itself is no longer declared here. It is
:class:`~repowise.cli.agent_targets.types.InstallLifecycle`, next to the
:class:`~repowise.cli.agent_targets.types.AgentTarget` descriptor whose surface
it is a subset of — one home for integration protocols rather than two that
drift.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.cli.agent_targets.types import InstallLifecycle

# When set (truthy), `repowise init` skips registering MCP servers / hooks in
# the user's *global* editor config (~/.claude/settings.json, Claude Desktop).
# Intended for headless / CI / benchmark indexing, where indexing many repos —
# or transient git worktrees — must not mutate the developer's global config or
# repoint the single global "repowise" MCP entry at a path that will be deleted.
# `init --no-editor-setup` is the interactive spelling of the same switch.
_SKIP_EDITOR_SETUP_ENV = "REPOWISE_SKIP_EDITOR_SETUP"


def is_editor_setup_disabled(no_editor_setup: bool = False) -> bool:
    """Whether global editor registration should be skipped for this run.

    Disabled when ``init --no-editor-setup`` was passed *or* the
    ``REPOWISE_SKIP_EDITOR_SETUP`` env var is set to anything but an explicit
    off value. The flag never re-enables what the env var turned off. It is
    per-run and never persisted to config, so a later ``init`` without it
    registers normally.

    Named with the ``is_`` prefix to stay distinct from
    :attr:`EditorSetupOutcome.editor_setup_disabled`, which records the same
    fact for one finished run.
    """
    if no_editor_setup:
        return True
    return os.environ.get(_SKIP_EDITOR_SETUP_ENV, "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


@dataclass(frozen=True)
class EditorSetupOutcome:
    """What editor setup actually ended up doing, for the completion panel.

    Snapshotted once at the authoritative moment — after client registration
    and the interactive hook offers have run — so the "what's next" panel can
    react to reality instead of guessing. Every field is a plain fact the panel
    turns into a suggestion (or stays quiet about).
    """

    #: repowise registered a Claude Code MCP entry this run (init always does,
    #: unless setup was skipped for a headless/CI run or by --no-editor-setup).
    claude_code_connected: bool = False
    #: the git post-commit auto-sync hook is present for this repo.
    autosync_hook_installed: bool = False
    #: the Claude Code distill command-rewrite hook is present (user-level).
    rewrite_hook_installed: bool = False
    #: the run could prompt (a TTY, not ``--yes``); when False the interactive
    #: hook offers were skipped, so the panel is the only place to surface them.
    interactive: bool = False
    #: no prior index existed before this run (first-time onboarding).
    first_index: bool = True
    #: editor setup was turned off for this run — via --no-editor-setup or the
    #: REPOWISE_SKIP_EDITOR_SETUP env var (CI/benchmark) — so nothing was wired up.
    editor_setup_disabled: bool = False


def detect_editor_setup_outcome(
    repo_path: Path,
    *,
    interactive: bool,
    first_index: bool,
    no_editor_setup: bool = False,
) -> EditorSetupOutcome:
    """Read the ground-truth editor-setup state for the completion panel.

    Called after registration and the hook offers, so what it reads is final.
    Every probe is a cheap local file read and is defensive: a failure degrades
    to "not set up" rather than crashing ``init``.
    """
    disabled = is_editor_setup_disabled(no_editor_setup)

    autosync = False
    try:
        from repowise.cli.hooks import status as _hook_status

        autosync = _hook_status(repo_path) == "installed"
    except Exception:
        pass

    # The distill rewrite hook is per-agent. Treat it as present when any agent
    # surface has it (Claude Code always, Codex only when detected), mirroring
    # `repowise hook rewrite status`, so a Codex-only user who set it up on
    # Codex is never nagged to install it again.
    rewrite = False
    try:
        from repowise.cli.agent_adapters.claude_code import ClaudeCodeAdapter
        from repowise.cli.agent_adapters.codex import CodexAdapter

        surfaces = [ClaudeCodeAdapter()]
        codex = CodexAdapter()
        if codex.detect():
            surfaces.append(codex)
        rewrite = any(surface.rewrite_hook_installed() for surface in surfaces)
    except Exception:
        pass

    return EditorSetupOutcome(
        claude_code_connected=not disabled,
        autosync_hook_installed=autosync,
        rewrite_hook_installed=rewrite,
        interactive=interactive,
        first_index=first_index,
        editor_setup_disabled=disabled,
    )


@dataclass(frozen=True)
class EditorSetupOptions:
    """Options shared across editor setup integrations."""

    disabled_project_files: frozenset[str] = field(default_factory=frozenset)
    project_file_overrides: dict[str, bool] = field(default_factory=dict)
    integration_overrides: dict[str, bool] = field(default_factory=dict)

    def with_disabled_project_file(self, project_file_id: str) -> EditorSetupOptions:
        """Return options with one managed project file disabled."""

        return EditorSetupOptions(
            disabled_project_files=self.disabled_project_files | {project_file_id},
            project_file_overrides=dict(self.project_file_overrides),
            integration_overrides=dict(self.integration_overrides),
        )

    def with_integration_override(
        self,
        integration_id: str,
        enabled: bool,
    ) -> EditorSetupOptions:
        """Return options with one editor integration explicitly enabled or disabled."""

        return EditorSetupOptions(
            disabled_project_files=self.disabled_project_files,
            project_file_overrides=dict(self.project_file_overrides),
            integration_overrides={**self.integration_overrides, integration_id: enabled},
        )


def _resolve_integrations(
    integrations: tuple[InstallLifecycle, ...] | None,
) -> tuple[InstallLifecycle, ...]:
    if integrations is not None:
        return integrations
    from repowise.cli.editor_integrations.defaults import get_default_editor_integrations

    return get_default_editor_integrations()


def resolve_editor_setup_options(
    *,
    disabled_project_files: Iterable[str] | None = None,
    project_file_overrides: Mapping[str, bool] | None = None,
    integration_overrides: Mapping[str, bool] | None = None,
) -> EditorSetupOptions:
    """Build setup options from the CLI flags.

    Used to also give every integration a chance to prompt, through a
    ``configure_options`` hook. That is gone: the prompting is now one
    checklist (:func:`select_agents_interactively`) built from the registry
    rather than one hand-written question per agent, so the hook had no
    implementation left that did anything.
    """

    return EditorSetupOptions(
        disabled_project_files=frozenset(disabled_project_files or ()),
        project_file_overrides=dict(project_file_overrides or {}),
        integration_overrides=dict(integration_overrides or {}),
    )


def select_agents_interactively(
    console_obj: Any,
    repo_path: Path,
    options: EditorSetupOptions,
) -> EditorSetupOptions:
    """Ask once which agents to set up, and fold the answer into *options*.

    Replaces the three sequential yes/no prompts each integration used to own
    — one per agent, in registry order, each hand-written. Two things were wrong
    with that shape and only one of them was cosmetic. The cosmetic one: Codex
    defaulted to *no* while its two neighbours defaulted to yes, so holding
    Enter through the sequence silently inverted in the middle, and the code
    apologised for it in a dim line. The structural one: a fourth agent meant a
    fourth prompt, written by hand, in a fourth module.

    Now the checklist is built from the registry and pre-ticked from detection,
    so an agent is offered because it is installed rather than because someone
    remembered to write a question for it.

    The mapping back onto options carries no per-agent knowledge: an unticked
    agent has its own ``project_file_id`` disabled and its integration id
    overridden off, both of which the descriptor supplies. A ticked one is
    overridden on. An id already carrying an explicit override (a CLI flag such
    as ``--codex``) is shown pre-ticked to match the flag, so accepting the
    checklist re-applies what the flag already said.
    """
    from repowise.cli.agent_targets.registry import default_selection, describe_agents
    from repowise.cli.ui.agent_selection import AgentChoice, interactive_agent_select

    # Offer only what this command can act on. The checklist is built from the
    # agent registry and the writing is done by the setup integrations, and
    # those are two lists: an agent can be registered (so it gets a matrix row,
    # a ``--target`` id and a ``doctor`` row) without ``init`` having a writer
    # for it. Showing the rest turns a ticked box into a silent no-op, which
    # reads as success because every agent that *was* written prints a line.
    #
    # ``repowise agents add --target=<id>`` is the command for those, and the
    # line below names it rather than leaving them invisible.
    rows = describe_agents(repo_path)
    installable = {integration.integration_id for integration in _resolve_integrations(None)}
    deferred = [row for row in rows if row["id"] not in installable]
    rows = [row for row in rows if row["id"] in installable]
    for row in deferred:
        if row["present"] or row["registrations"]:
            console_obj.print(
                f"  [dim]{row['display_name']} detected. Set it up with "
                f"[bold]repowise agents add --target={row['id']}[/bold][/dim]"
            )

    ticked = default_selection(rows)
    for row in rows:
        flagged = options.integration_overrides.get(row["id"])
        if flagged is not None:
            ticked = (ticked | {row["id"]}) if flagged else (ticked - {row["id"]})

    chosen = interactive_agent_select(
        console_obj,
        [
            AgentChoice(
                id=row["id"],
                display_name=row["display_name"],
                detail=(
                    "already wired"
                    if row["registrations"]
                    else ("installed" if row["present"] else "not detected")
                ),
                enabled=row["id"] in ticked,
            )
            for row in rows
        ],
    )
    if chosen is None:
        # stdin claimed a terminal and then returned EOF. Keep the defaults.
        return options

    for row in rows:
        enabled = row["id"] in chosen
        options = options.with_integration_override(row["id"], enabled)
        if not enabled:
            options = options.with_disabled_project_file(row["project_file_id"])
    return options


def write_editor_project_files(
    console_obj: Any,
    repo_path: Path,
    *,
    options: EditorSetupOptions | None = None,
    disabled_project_files: Iterable[str] | None = None,
    integrations: tuple[InstallLifecycle, ...] | None = None,
) -> None:
    """Write common MCP config and project-local editor files."""

    from repowise.cli.mcp_config import save_mcp_config

    save_mcp_config(repo_path)
    resolved_options = options or EditorSetupOptions(
        disabled_project_files=frozenset(disabled_project_files or ()),
    )
    for integration in _resolve_integrations(integrations):
        integration.write_project_files(console_obj, repo_path, resolved_options)


def register_editor_clients(
    console_obj: Any,
    repo_path: Path,
    *,
    no_editor_setup: bool = False,
    integrations: tuple[InstallLifecycle, ...] | None = None,
) -> None:
    """Register editor clients with repowise MCP and hooks where supported."""

    if is_editor_setup_disabled(no_editor_setup):
        return
    for integration in _resolve_integrations(integrations):
        integration.register_client(console_obj, repo_path)


def refresh_editor_project_files(
    console_obj: Any,
    repo_path: Path,
    *,
    options: EditorSetupOptions | None = None,
    integrations: tuple[InstallLifecycle, ...] | None = None,
) -> None:
    """Refresh editor-managed project files without rewriting common MCP config."""

    resolved_options = options or EditorSetupOptions()
    for integration in _resolve_integrations(integrations):
        integration.refresh_project_files(console_obj, repo_path, resolved_options)
