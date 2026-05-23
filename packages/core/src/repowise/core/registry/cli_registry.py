"""CLI registry — collect commands, apply them to the root Click group.

The OSS CLI used to hard-code one ``cli.add_command(...)`` call per
command at the bottom of :mod:`repowise.cli.main`. That works, but a
third-party package that wants to add a subcommand has to monkey-patch
the root group at import time and hope its import runs early enough.

This registry is a thin indirection. Each command (OSS or third-party)
registers itself once on import; the CLI entry point calls
:meth:`CLIRegistry.apply` exactly once, after all commands have had a
chance to register, and the resulting Click group looks identical to
the hard-coded version.

Usage::

    # OSS or plugin side
    from repowise.core.registry import register_command
    register_command(my_command)
    register_command(my_subcommand, parent=some_group)

    # CLI entry point
    from repowise.core.registry import cli_registry
    cli_registry.apply(cli)

Registration order is preserved, matching the hard-coded behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import click


class CLIRegistry:
    """Holds (parent, command) pairs until :meth:`apply` attaches them."""

    def __init__(self) -> None:
        self._entries: list[tuple[click.BaseCommand | None, click.BaseCommand]] = []
        self._applied_to: list[int] = []

    def register(
        self,
        command: click.BaseCommand,
        *,
        parent: click.BaseCommand | None = None,
    ) -> None:
        """Schedule *command* for attachment to *parent* (default: root)."""
        self._entries.append((parent, command))

    def apply(self, root: click.BaseCommand) -> click.BaseCommand:
        """Attach every registered command. Returns *root* for chaining.

        Idempotent per *root* — calling ``apply`` twice with the same
        Click group is a no-op. Passing a different group registers
        every entry against that group too (useful for tests that build
        an isolated root).
        """
        root_id = id(root)
        if root_id in self._applied_to:
            return root
        for parent, command in self._entries:
            target = parent if parent is not None else root
            target.add_command(command)  # type: ignore[attr-defined]
        self._applied_to.append(root_id)
        return root

    def reset(self) -> None:
        """Drop every registered entry. Used by tests."""
        self._entries.clear()
        self._applied_to.clear()

    def commands(self) -> list[click.BaseCommand]:
        """Return every registered command. Used by tests."""
        return [cmd for _, cmd in self._entries]


cli_registry = CLIRegistry()
"""Process-wide default registry used by the OSS CLI."""


def register_command(
    command: click.BaseCommand,
    *,
    parent: click.BaseCommand | None = None,
) -> None:
    """Module-level convenience over :meth:`CLIRegistry.register`."""
    cli_registry.register(command, parent=parent)


__all__ = ["CLIRegistry", "cli_registry", "register_command"]
