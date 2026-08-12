"""The root CLI group must dispatch a subcommand without importing the rest.

``repowise.cli.main`` used to import all ~35 command modules at startup —
1.1s paid by every invocation, including every agent hook fire, to run one
subcommand. Commands are now registered as ``"module:attr"`` strings and
resolved one at a time.

Three things have to stay true for that to be safe, and each has a test
below: the static names must match the real Click names (nothing else
checks a string table), dispatch must import exactly one command module,
and ``--help`` must still list every command.
"""

from __future__ import annotations

import subprocess
import sys

import click
import pytest
from click.testing import CliRunner

from repowise.cli._instrumented_group import InstrumentedGroup
from repowise.cli.main import _OSS_COMMANDS, cli
from repowise.core.registry import CLIRegistry, LazyCommand

#: The CLI surface, frozen independently of ``_OSS_COMMANDS``.
#:
#: Deliberately duplicated: every other assertion here derives its
#: expectation from ``_OSS_COMMANDS`` itself, so deleting a row would make
#: the whole file agree that the command never existed. This is the one
#: tripwire that catches a dropped command. Adding or removing a CLI
#: command is meant to require editing it.
_EXPECTED_NAMES = frozenset(
    {
        "agents", "ask", "augment", "context", "corrections", "costs", "coverage",
        "dead-code", "decision", "delete", "distill", "doctor", "expand",
        "export", "generate", "generate-claude-md", "health", "hook",
        "impacted-tests", "init", "login", "logout", "mcp", "reindex", "restyle",
        "risk", "saved", "search", "security", "serve", "status", "symbol",
        "telemetry", "update", "watch", "whats-new", "whoami", "why",
        "wiki-styles", "workspace",
    }
)  # fmt: skip


def test_the_command_surface_is_unchanged() -> None:
    registered = {name for name, _target in _OSS_COMMANDS}
    assert registered == _EXPECTED_NAMES


@pytest.mark.parametrize(("name", "target"), _OSS_COMMANDS)
def test_every_lazy_name_matches_the_real_command_name(name: str, target: str) -> None:
    """A wrong name in the table is invisible until the command is run.

    ``list_commands`` reports the static name, so a typo would advertise a
    command that fails to dispatch — and only for that one command.
    """
    command = LazyCommand(name, f"repowise.cli.commands.{target}").load()
    assert command.name == name


def test_help_lists_every_registered_command() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for name, _target in _OSS_COMMANDS:
        assert f"\n  {name} " in result.output or f"\n  {name}\n" in result.output


def test_list_commands_imports_nothing() -> None:
    """Dispatch and shell completion read names only — no module loads."""
    probe = (
        "import repowise.cli.main as m, click, sys; "
        "ctx = click.Context(m.cli); "
        "names = m.cli.list_commands(ctx); "
        "loaded = [x for x in sys.modules if x.startswith('repowise.cli.commands.')]; "
        "print(len(names)); print(loaded)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    lines = out.stdout.strip().splitlines()
    assert int(lines[-2]) == len(_OSS_COMMANDS)
    assert lines[-1] == "[]", f"listing command names imported: {lines[-1]}"


def _dispatch_and_report_loaded(argv: list[str]) -> str:
    """Run *argv* in a fresh interpreter; return the command modules it loaded.

    A subprocess because ``sys.modules`` is shared for the whole pytest
    session — an in-process check would be answering for every earlier test
    as well. The exit code is asserted so a command that imports and then
    crashes cannot pass as "only imported one module".
    """
    probe = (
        "import sys; "
        "from repowise.cli.main import cli; "
        "from click.testing import CliRunner; "
        f"res = CliRunner().invoke(cli, {argv!r}); "
        "assert res.exit_code == 0, res.output; "
        "print(sorted({m.split('.')[3] for m in sys.modules "
        "if m.startswith('repowise.cli.commands.')}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    return out.stdout.strip().splitlines()[-1]


def test_dispatch_imports_only_the_invoked_command() -> None:
    """The whole point: running one command must not load the other 34."""
    loaded = _dispatch_and_report_loaded(["expand", "--help"])
    assert loaded == "['expand_cmd']", f"dispatching `expand` also imported: {loaded}"


def test_a_group_subcommand_resolves() -> None:
    """Groups (decision/coverage/workspace/hook) resolve once the group loads."""
    result = CliRunner().invoke(cli, ["hook", "--help"])
    assert result.exit_code == 0
    assert "stats" in result.output


def test_unknown_command_still_errors() -> None:
    result = CliRunner().invoke(cli, ["definitely-not-a-command"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_a_plain_group_gets_the_command_eagerly() -> None:
    """A root that cannot defer must still end up with every command.

    Third-party code (and tests) build isolated ``click.Group`` roots. The
    registry resolves lazy entries against those rather than dropping them.
    """
    registry = CLIRegistry()
    registry.register_lazy("expand", "repowise.cli.commands.expand_cmd:expand_command")
    root = click.Group("root")
    registry.apply(root)
    assert "expand" in root.commands


def test_instrumented_group_caches_the_resolved_command() -> None:
    group = InstrumentedGroup("root")
    group.add_lazy_command("expand", "repowise.cli.commands.expand_cmd:expand_command")
    ctx = click.Context(group)
    first = group.get_command(ctx, "expand")
    assert first is not None
    assert group.get_command(ctx, "expand") is first
    assert "expand" in group.commands


def test_the_last_registration_of_a_name_wins() -> None:
    """Plugin override precedence must not depend on lazy vs eager.

    ``click.Group.add_command`` overwrites, so a plugin registering
    ``status`` after the OSS CLI has always taken over. Holding lazy names
    in a second dict would quietly invert that in whichever direction the
    lookup happened to check first — in both directions here.
    """
    plugin = click.Command("status", callback=lambda: None)
    group = InstrumentedGroup("root")
    ctx = click.Context(group)

    group.add_lazy_command("status", "repowise.cli.commands.status_cmd:status_command")
    group.add_command(plugin)
    assert group.get_command(ctx, "status") is plugin

    group.add_lazy_command("status", "repowise.cli.commands.status_cmd:status_command")
    assert group.get_command(ctx, "status") is not plugin

    # Either way the name is listed exactly once.
    assert group.list_commands(ctx).count("status") == 1


def test_a_resolved_command_does_not_outlive_a_re_registration() -> None:
    """``get_command`` caches into ``self.commands``; re-registering must win."""
    group = InstrumentedGroup("root")
    ctx = click.Context(group)
    group.add_lazy_command("thing", "repowise.cli.commands.status_cmd:status_command")
    first = group.get_command(ctx, "thing")
    group.add_lazy_command("thing", "repowise.cli.commands.expand_cmd:expand_command")
    second = group.get_command(ctx, "thing")
    assert first is not None and second is not None
    assert second is not first


def test_the_command_registry_pulls_no_structlog() -> None:
    """``core.registry`` is what the CLI entry point imports.

    ``pipeline_hooks`` held a module-level ``structlog.get_logger``, worth
    192ms (structlog pulls rich and asyncio) charged to every ``repowise``
    invocation for one warning in a broken-plugin path.
    """
    probe = (
        "import sys, repowise.core.registry; "
        "print(sorted(m for m in sys.modules if m.startswith(('structlog', 'rich'))))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip().splitlines()[-1] == "[]", out.stdout


def test_telemetry_tail_match_does_not_resolve_every_command() -> None:
    """``InstrumentedGroup.invoke`` matches the arg tail for telemetry.

    It resolves the one invoked command to read that command's own
    subcommands. Resolving all of them there would silently undo laziness.
    """
    loaded = _dispatch_and_report_loaded(["hook", "stats", "--help"])
    assert loaded == "['hook_cmd']", loaded
