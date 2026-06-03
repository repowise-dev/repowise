"""AI editor setup orchestration for repowise init.

The indexing command should not know the details of each editor's config files,
global settings, or managed instruction files.  This module keeps that product
setup layer behind a small integration interface; concrete editor integrations
live in ``repowise.cli.editor_integrations``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# When set (truthy), `repowise init` skips registering MCP servers / hooks in
# the user's *global* editor config (~/.claude/settings.json, Claude Desktop).
# Intended for headless / CI / benchmark indexing, where indexing many repos —
# or transient git worktrees — must not mutate the developer's global config or
# repoint the single global "repowise" MCP entry at a path that will be deleted.
_SKIP_EDITOR_SETUP_ENV = "REPOWISE_SKIP_EDITOR_SETUP"


def _editor_setup_disabled() -> bool:
    return os.environ.get(_SKIP_EDITOR_SETUP_ENV, "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


@dataclass(frozen=True)
class EditorSetupOptions:
    """Options shared across editor setup integrations."""

    disabled_project_files: frozenset[str] = field(default_factory=frozenset)
    prompt_for_project_files: bool = False
    project_file_overrides: dict[str, bool] = field(default_factory=dict)
    integration_overrides: dict[str, bool] = field(default_factory=dict)

    def with_disabled_project_file(self, project_file_id: str) -> EditorSetupOptions:
        """Return options with one managed project file disabled."""

        return EditorSetupOptions(
            disabled_project_files=self.disabled_project_files | {project_file_id},
            prompt_for_project_files=self.prompt_for_project_files,
            project_file_overrides=dict(self.project_file_overrides),
            integration_overrides=dict(self.integration_overrides),
        )

    def with_project_file_override(
        self,
        project_file_id: str,
        enabled: bool,
    ) -> EditorSetupOptions:
        """Return options with one managed project file explicitly enabled or disabled."""

        return EditorSetupOptions(
            disabled_project_files=self.disabled_project_files,
            prompt_for_project_files=self.prompt_for_project_files,
            project_file_overrides={**self.project_file_overrides, project_file_id: enabled},
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
            prompt_for_project_files=self.prompt_for_project_files,
            project_file_overrides=dict(self.project_file_overrides),
            integration_overrides={**self.integration_overrides, integration_id: enabled},
        )


class EditorSetupIntegration(Protocol):
    """Setup hooks implemented by each AI editor integration."""

    def configure_options(
        self,
        console_obj: Any,
        options: EditorSetupOptions,
    ) -> EditorSetupOptions:
        """Let the integration prompt or adjust setup options before writing files."""
        ...

    def write_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        """Write project-local config or instruction files for this editor."""
        ...

    def register_client(self, console_obj: Any, repo_path: Path) -> None:
        """Register global or user-level client configuration for this editor."""
        ...

    def refresh_project_files(
        self,
        console_obj: Any,
        repo_path: Path,
        options: EditorSetupOptions,
    ) -> None:
        """Refresh managed project files after repository content changes."""
        ...


def _resolve_integrations(
    integrations: tuple[EditorSetupIntegration, ...] | None,
) -> tuple[EditorSetupIntegration, ...]:
    if integrations is not None:
        return integrations
    from repowise.cli.editor_integrations.defaults import get_default_editor_integrations

    return get_default_editor_integrations()


def resolve_editor_setup_options(
    console_obj: Any,
    *,
    disabled_project_files: Iterable[str] | None = None,
    prompt_for_project_files: bool = False,
    project_file_overrides: Mapping[str, bool] | None = None,
    integration_overrides: Mapping[str, bool] | None = None,
    integrations: tuple[EditorSetupIntegration, ...] | None = None,
) -> EditorSetupOptions:
    """Build setup options, allowing integrations to own their prompts."""

    options = EditorSetupOptions(
        disabled_project_files=frozenset(disabled_project_files or ()),
        prompt_for_project_files=prompt_for_project_files,
        project_file_overrides=dict(project_file_overrides or {}),
        integration_overrides=dict(integration_overrides or {}),
    )
    for integration in _resolve_integrations(integrations):
        options = integration.configure_options(console_obj, options)
    return options


def write_editor_project_files(
    console_obj: Any,
    repo_path: Path,
    *,
    options: EditorSetupOptions | None = None,
    disabled_project_files: Iterable[str] | None = None,
    integrations: tuple[EditorSetupIntegration, ...] | None = None,
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
    integrations: tuple[EditorSetupIntegration, ...] | None = None,
) -> None:
    """Register editor clients with repowise MCP and hooks where supported."""

    if _editor_setup_disabled():
        return
    for integration in _resolve_integrations(integrations):
        integration.register_client(console_obj, repo_path)


def refresh_editor_project_files(
    console_obj: Any,
    repo_path: Path,
    *,
    options: EditorSetupOptions | None = None,
    integrations: tuple[EditorSetupIntegration, ...] | None = None,
) -> None:
    """Refresh editor-managed project files without rewriting common MCP config."""

    resolved_options = options or EditorSetupOptions()
    for integration in _resolve_integrations(integrations):
        integration.refresh_project_files(console_obj, repo_path, resolved_options)
