"""What init does to the per-user ``repowise`` MCP entries, and the one place it asks.

Claude Code (top-level ``mcpServers`` in ``~/.claude.json``) and Claude Desktop each hold a
single ``repowise`` server key shared by every repo on the machine. init adds
it when absent and heals it when the repo it names is gone, but never moves an
entry that serves another repo (``claude_config.planned_global_entry`` owns
that rule). :func:`read_global_mcp_target` is the read-only probe a start
screen can show; :func:`confirm_global_mcp` is the one place init asks.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from repowise.cli.editor_integrations import claude_config

logger = logging.getLogger(__name__)

NOT_SET = "not set"
#: Writes the user-level entries for this repo, moving any that serve another.
REGISTER_LATER_COMMAND = "repowise agents add --target=claude-code --scope=user"


@dataclass(frozen=True)
class GlobalMcpEntry:
    """One client's stored ``repowise`` entry and what init would write there."""

    client: str
    config_path: Path
    target: str | None
    command: str | None
    present: bool = False
    #: The entry init writes, or None when it keeps the stored one.
    planned: dict | None = None

    @property
    def kept(self) -> bool:
        return self.present and self.planned is None

    @property
    def planned_target(self) -> str | None:
        return claude_config._entry_repo_target(self.planned) if self.planned else None

    @property
    def moves(self) -> bool:
        """Whether the write adds the entry or changes the repo it serves."""
        if self.planned is None:
            return False
        if not self.present:
            return True
        new = self.planned_target
        if self.target is None or new is None:
            return self.target != new
        return not claude_config._same_target(self.target, Path(new))

    @property
    def command_changes(self) -> bool:
        return (
            self.planned is not None
            and self.present
            and self.command != self.planned.get("command")
        )


def _serves(target: str | None) -> str:
    return target or "the repo it is started in"


@dataclass(frozen=True)
class GlobalMcpTarget:
    """Each client's entry now, and what registration writes."""

    entries: tuple[GlobalMcpEntry, ...]

    @property
    def repoints(self) -> bool:
        """Whether any client gains an entry or has one moved off a missing path."""
        return any(e.moves for e in self.entries)

    @property
    def command_changes(self) -> bool:
        return any(e.command_changes for e in self.entries)

    def summary_lines(self) -> list[str]:
        """``<client> repowise MCP entry: now X, will serve Y`` for each entry that moves."""
        lines = []
        for e in self.entries:
            if not e.moves:
                continue
            now = (e.target or NOT_SET) if e.present else NOT_SET
            if e.present and e.target and not Path(e.target).is_dir():
                now += " (missing)"
            lines.append(
                f"{e.client} repowise MCP entry now points at {now}, "
                f"will serve {_serves(e.planned_target)}"
            )
        return lines

    def kept_lines(self) -> list[str]:
        """One line per entry init leaves serving another repo."""
        lines = []
        for e in self.entries:
            if not e.kept:
                continue
            serves = f"serves {e.target}" if e.target else "is a custom entry"
            if e.client == "Claude Desktop":
                lines.append(
                    f"Claude Desktop's repowise entry {serves} and is left as is; "
                    f"'{REGISTER_LATER_COMMAND}' here rewrites both Claude entries for this repo."
                )
            else:
                lines.append(
                    f"{e.client}'s global repowise entry {serves}; "
                    "this repo's .mcp.json covers this repo."
                )
        return lines


def read_global_mcp_target(repo_path: Path) -> GlobalMcpTarget:
    """Read-only: what each global config holds and what init would write.

    Lists the clients registration would write: Claude Code unless the
    repowise plugin serves it, Claude Desktop only when installed, the same
    rules the writers apply.
    """
    configs: list[tuple[str, Path, bool]] = []
    if not claude_config.claude_code_plugin_installed():
        configs.append(("Claude Code", claude_config._claude_code_user_config_path(), True))
    desktop = claude_config._claude_desktop_config_path()
    if desktop is not None and desktop.parent.exists():
        configs.append(("Claude Desktop", desktop, False))

    entries = []
    for client, path, follows in configs:
        stored = claude_config._stored_repowise_entry(path)
        entries.append(
            GlobalMcpEntry(
                client=client,
                config_path=path,
                target=claude_config._entry_repo_target(stored) if stored else None,
                command=stored.get("command") if stored else None,
                present=stored is not None,
                planned=claude_config.planned_global_entry(
                    stored, repo_path, follows_launch_dir=follows
                ),
            )
        )
    return GlobalMcpTarget(entries=tuple(entries))


def _probe(repo_path: Path) -> GlobalMcpTarget | None:
    """The probe, or None when it fails. A failure never blocks registration."""
    try:
        return read_global_mcp_target(repo_path)
    except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        logger.debug("global MCP probe failed for %s: %s", repo_path, exc)
        return None


def _print_command_move(console_obj: Any, target: GlobalMcpTarget) -> None:
    for e in target.entries:
        if e.command_changes and not e.moves and e.planned is not None:
            console_obj.print(
                f"  [dim]{e.client} repowise MCP entry stays on "
                f"{_serves(e.planned_target)}; its command moves to "
                f"{e.planned.get('command')}.[/dim]"
            )


def _ask_to_write(console_obj: Any) -> bool:
    """Prompt, default yes. Ctrl-C or EOF declines, like init's other offers."""
    try:
        agreed = click.confirm("  Write it?", default=True)
    except (click.Abort, EOFError):
        agreed = False
    if not agreed:
        console_obj.print(
            "  [dim]Global Claude config left as is. Project files still connect "
            f"agents opened in this repo. Run '{REGISTER_LATER_COMMAND}' to write "
            "it later.[/dim]"
        )
    return agreed


def _write_consent(console_obj: Any, target: GlobalMcpTarget, *, yes: bool) -> bool:
    """Print what changes; ask only when someone can answer."""
    for line in target.summary_lines():
        console_obj.print(f"  {line}")
    if yes or not sys.stdin.isatty():
        return True
    return _ask_to_write(console_obj)


def confirm_global_mcp(
    console_obj: Any,
    repo_path: Path,
    *,
    yes: bool = False,
    no_editor_setup: bool = False,
) -> bool:
    """Whether init may write user-level client config for *repo_path*.

    An entry serving another repo is never moved, so it only gets a line
    saying so. Asks (default yes) only when an entry would be added or moved
    off a missing path and someone can answer. ``--yes`` and a non-terminal
    stdin keep registering, the documented scripted path, and print what
    changes. A decline leaves every user-level file alone; project files are
    written either way.
    """
    from repowise.cli.editor_setup import is_editor_setup_disabled

    if is_editor_setup_disabled(no_editor_setup):
        return False
    target = _probe(repo_path)
    if target is None:
        return True
    for line in target.kept_lines():
        console_obj.print(f"  [dim]{line}[/dim]")
    if not target.repoints:
        _print_command_move(console_obj, target)
        return True
    return _write_consent(console_obj, target, yes=yes)
