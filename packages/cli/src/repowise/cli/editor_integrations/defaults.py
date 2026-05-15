"""Default editor setup integration registry."""

from __future__ import annotations

from repowise.cli.editor_setup import EditorSetupIntegration

from .claude import ClaudeCodeSetup


def get_default_editor_integrations() -> tuple[EditorSetupIntegration, ...]:
    """Return the editor integrations enabled by default today."""

    return (ClaudeCodeSetup(),)
