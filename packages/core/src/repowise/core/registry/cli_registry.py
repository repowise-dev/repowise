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
    from repowise.core.registry import register_command, register_lazy_command
    register_command(my_command)
    register_command(my_subcommand, parent=some_group)
    register_lazy_command("my-command", "my_pkg.commands.mine:my_command")

    # CLI entry point
    from repowise.core.registry import cli_registry
    cli_registry.apply(cli)

Registration order is preserved, matching the hard-coded behavior.

Eager vs lazy
-------------

:func:`register_command` takes an already-imported Click object, so every
registered command's module is imported before the CLI can dispatch even
one of them. For the OSS CLI that was ~1.1s of import cost paid by every
single invocation. :func:`register_lazy_command` takes a
``"module:attr"`` string instead: the name is known without importing
anything, and the module is imported only when that command is the one
being run.

Laziness needs a root group that knows how to hold an unresolved name —
:class:`repowise.cli._instrumented_group.InstrumentedGroup` implements
``add_lazy_command`` for this. Against any other Click group (a plain
``click.Group`` built by a test, or a non-root ``parent``) a lazy entry
resolves immediately and attaches eagerly, so behavior is identical and
only the timing differs.

Either way the last registration of a name wins, matching
``click.Group.add_command`` — a plugin registering ``status`` after the
OSS CLI overrides it whether either side is lazy or eager.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    import click


class LazyCommand(NamedTuple):
    """A command named now and imported later.

    *target* is a ``"module:attr"`` string, the same shape setuptools
    uses for console-script entry points.
    """

    name: str
    target: str

    def load(self) -> click.BaseCommand:
        """Import the module and return the Click object it holds."""
        module_name, _, attr = self.target.partition(":")
        return getattr(importlib.import_module(module_name), attr)


class CLIRegistry:
    """Holds (parent, command) pairs until :meth:`apply` attaches them."""

    def __init__(self) -> None:
        self._entries: list[tuple[click.BaseCommand | None, click.BaseCommand | LazyCommand]] = []
        self._applied_to: list[int] = []

    def register(
        self,
        command: click.BaseCommand,
        *,
        parent: click.BaseCommand | None = None,
    ) -> None:
        """Schedule *command* for attachment to *parent* (default: root)."""
        self._entries.append((parent, command))

    def register_lazy(
        self,
        name: str,
        target: str,
        *,
        parent: click.BaseCommand | None = None,
    ) -> None:
        """Schedule the command at *target* under *name*, importing nothing.

        *target* is ``"module:attr"``. *name* must match the Click
        command's own name, since it is what the root group lists and
        dispatches on before the module is ever loaded.
        """
        self._entries.append((parent, LazyCommand(name, target)))

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
            if isinstance(command, LazyCommand):
                add_lazy = getattr(target, "add_lazy_command", None)
                if add_lazy is not None:
                    add_lazy(command.name, command.target)
                    continue
                # A target that cannot defer still gets the command, just
                # at the cost of importing it now.
                command = command.load()
            target.add_command(command)  # type: ignore[attr-defined]
        self._applied_to.append(root_id)
        return root

    def reset(self) -> None:
        """Drop every registered entry. Used by tests."""
        self._entries.clear()
        self._applied_to.clear()

    def commands(self) -> list[click.BaseCommand]:
        """Return every registered command, importing lazy ones. Used by tests."""
        return [cmd.load() if isinstance(cmd, LazyCommand) else cmd for _, cmd in self._entries]


cli_registry = CLIRegistry()
"""Process-wide default registry used by the OSS CLI."""


def register_command(
    command: click.BaseCommand,
    *,
    parent: click.BaseCommand | None = None,
) -> None:
    """Module-level convenience over :meth:`CLIRegistry.register`."""
    cli_registry.register(command, parent=parent)


def register_lazy_command(
    name: str,
    target: str,
    *,
    parent: click.BaseCommand | None = None,
) -> None:
    """Module-level convenience over :meth:`CLIRegistry.register_lazy`."""
    cli_registry.register_lazy(name, target, parent=parent)


__all__ = [
    "CLIRegistry",
    "LazyCommand",
    "cli_registry",
    "register_command",
    "register_lazy_command",
]
