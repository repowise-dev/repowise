"""Default editor integrations, and the legacy CLI flag mapping.

Two different things live here and it is worth being explicit about which is
which, because one of them is a dead end by design.

:func:`get_default_editor_integrations` is the live registry of setup
integrations ``init`` and ``update`` drive.

The three ``get_default_*`` functions below map **legacy CLI flags**
(``--no-claude-md``, ``--agents-md``, ``--codex``) onto the ids those flags
control. Each takes a keyword argument named after one agent, which is the
shape that does not scale: at three agents it is mildly redundant, at twelve it
is the flat per-host chain that made a competitor's installer 7,446 lines. So
the ids are no longer restated here — each is read from the agent's own
descriptor, and these functions are now a thin translation layer over that.

**A new agent adds nothing to this file.** It gets a descriptor and a registry
line; if it also wants a CLI flag, the flag joins the general mechanism rather
than growing a fourth keyword function. That is the whole point of the change,
and it is why these three keep their signatures instead of being deleted: they
have direct callers in ``init`` and ``update`` and direct test coverage, and
breaking a public surface adds risk to a rewrite whose value is that it changes
nothing observable.
"""

from __future__ import annotations

from repowise.cli.agent_targets.types import InstallLifecycle

from .claude import ClaudeCodeSetup
from .codex import CodexSetup
from .vscode import VSCodeSetup


def _project_file_id(target_id: str) -> str:
    """The ``editor_files`` config key owned by *target_id*'s descriptor.

    Read from the registry rather than restated, so an agent's instruction-file
    key is defined in exactly one place — the agent's own module.
    """
    from repowise.cli.agent_targets.registry import get_target

    target = get_target(target_id)
    if target is None:  # pragma: no cover - registry and callers move together
        raise LookupError(f"no agent target registered for {target_id!r}")
    return target.project_file_id


def get_default_editor_integrations() -> tuple[InstallLifecycle, ...]:
    """Return the editor integrations enabled by default today."""

    return (ClaudeCodeSetup(), CodexSetup(), VSCodeSetup())


def get_default_disabled_project_files(*, no_claude_md: bool = False) -> tuple[str, ...]:
    """Map legacy CLI editor-file flags to integration-owned project file ids."""

    disabled: list[str] = []
    if no_claude_md:
        disabled.append(_project_file_id("claude-code"))
    return tuple(disabled)


def get_default_project_file_overrides(
    *,
    agents_md: bool | None = None,
) -> dict[str, bool]:
    """Map legacy/default CLI editor-file flags to integration-owned file ids."""

    overrides: dict[str, bool] = {}
    if agents_md is not None:
        overrides[_project_file_id("codex")] = agents_md
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
