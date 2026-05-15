"""Default editor setup integration registry."""

from __future__ import annotations

from repowise.cli.editor_setup import EditorSetupIntegration

from .claude import ClaudeCodeSetup
from .codex import CodexSetup


def get_default_editor_integrations() -> tuple[EditorSetupIntegration, ...]:
    """Return the editor integrations enabled by default today."""

    return (ClaudeCodeSetup(), CodexSetup())


def get_default_disabled_project_files(*, no_claude_md: bool = False) -> tuple[str, ...]:
    """Map legacy CLI editor-file flags to integration-owned project file ids."""

    disabled: list[str] = []
    if no_claude_md:
        disabled.append(ClaudeCodeSetup.project_file_id)
    return tuple(disabled)


def get_default_project_file_overrides(
    *,
    agents_md: bool | None = None,
) -> dict[str, bool]:
    """Map legacy/default CLI editor-file flags to integration-owned file ids."""

    overrides: dict[str, bool] = {}
    if agents_md is not None:
        overrides[CodexSetup.project_file_id] = agents_md
    return overrides


def get_default_integration_overrides(
    *,
    codex_setup: bool | None = None,
) -> dict[str, bool]:
    """Map CLI setup toggles to integration ids."""

    overrides: dict[str, bool] = {}
    if codex_setup is not None:
        overrides[CodexSetup.integration_id] = codex_setup
    return overrides
