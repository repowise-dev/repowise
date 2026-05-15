"""AI editor setup orchestration for repowise init.

The indexing command should not know the details of each editor's config files,
global settings, or managed instruction files.  This module keeps that product
setup layer behind a small integration interface; concrete editor integrations
live in ``repowise.cli.editor_integrations``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class EditorSetupOptions:
    """Options shared across editor setup integrations."""

    disabled_project_files: frozenset[str] = field(default_factory=frozenset)


class EditorSetupIntegration(Protocol):
    """Setup hooks implemented by each AI editor integration."""

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


def _resolve_integrations(
    integrations: tuple[EditorSetupIntegration, ...] | None,
) -> tuple[EditorSetupIntegration, ...]:
    if integrations is not None:
        return integrations
    from repowise.cli.editor_integrations.defaults import get_default_editor_integrations

    return get_default_editor_integrations()


def write_editor_project_files(
    console_obj: Any,
    repo_path: Path,
    *,
    disabled_project_files: Iterable[str] | None = None,
    integrations: tuple[EditorSetupIntegration, ...] | None = None,
) -> None:
    """Write common MCP config and project-local editor files."""

    from repowise.cli.mcp_config import save_mcp_config

    save_mcp_config(repo_path)
    options = EditorSetupOptions(
        disabled_project_files=frozenset(disabled_project_files or ()),
    )
    for integration in _resolve_integrations(integrations):
        integration.write_project_files(console_obj, repo_path, options)


def register_editor_clients(
    console_obj: Any,
    repo_path: Path,
    *,
    integrations: tuple[EditorSetupIntegration, ...] | None = None,
) -> None:
    """Register editor clients with repowise MCP and hooks where supported."""

    for integration in _resolve_integrations(integrations):
        integration.register_client(console_obj, repo_path)
