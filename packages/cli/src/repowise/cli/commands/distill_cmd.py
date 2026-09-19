"""``repowise distill`` — run a command and emit a distilled rendering.

Executes the real command, captures its output, and prints the compact
errors-first rendering with an omission marker pointing at the stashed raw
output (``repowise expand <ref>`` round-trips it). The wrapped command's
exit code is always preserved, so this is a drop-in replacement in scripts
and agent tool calls alike.

The wrapped command runs in the shell it was *written* for, not the one this
host happens to default to. ``--shell posix`` on Windows executes through the
agent's own Git Bash rather than ``cmd.exe``, where ``head`` does not exist,
``&&`` binds differently and a single-quoted chain is not a quoted token at
all. That is the constraint the rewrite hook used to defend by declining to
rewrite anything with a metacharacter on Windows.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import click

from repowise.cli.agent_adapters.base import SHELL_POSIX, SHELL_POWERSHELL
from repowise.cli.helpers import find_repowise_repo_root


@click.command(
    "distill",
    # allow_interspersed_args=False keeps option parsing strictly *before* the
    # wrapped command — a command that itself takes --source is never touched.
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
    short_help="Run a command and print a compact, reversible rendering of its output.",
)
@click.option(
    "--source",
    default="cli",
    hidden=True,
    help="Ledger surface label (the rewrite hook tags hook-bash / hook-powershell).",
)
@click.option(
    "--shell",
    "dialect",
    default=None,
    hidden=True,
    type=click.Choice([SHELL_POSIX, SHELL_POWERSHELL]),
    help=(
        "Shell dialect COMMAND was written for. Unlike --source this is not a "
        "label: it decides which interpreter executes the command. Omitted "
        "means this host's default shell."
    ),
)
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
def distill_command(source: str, dialect: str | None, command: tuple[str, ...]) -> None:
    """Run COMMAND and print a distilled rendering of its output.

    Examples:

        repowise distill git status

        repowise distill pytest tests/unit -x

        repowise distill npm run build

    Noise (pass parades, progress spam, hint boilerplate) is dropped; errors,
    failures, and summaries always survive. Dropped content is stored in
    .repowise/ and referenced by an inline ``[repowise#<ref>: ...]`` marker —
    restore it with ``repowise expand <ref>``. On any filter problem the raw
    output is printed unchanged. The command's exit code is preserved.
    """
    # A POSIX-dialect command needs POSIX rendering wherever it runs; on a
    # POSIX host every command is one, which is what keeps today's behaviour
    # for plain `repowise distill ...` from a terminal.
    posix_dialect = dialect == SHELL_POSIX
    try:
        command_str = _render_command(command, posix=posix_dialect or sys.platform != "win32")
    except UnrenderableCommandError as exc:
        # Refusing is the safe half of the trade: running a command the user
        # did not type is worse than not running one they did.
        raise click.ClickException(str(exc)) from exc
    if posix_dialect and sys.platform == "win32":
        shell_exe = _posix_shell()
        if shell_exe is None:
            # cmd.exe would accept most of these and mean something else by
            # them, which is the failure this whole path exists to avoid. Not
            # running the command is recoverable; running a different one is
            # not.
            raise click.ClickException(
                "this command is POSIX shell syntax and no POSIX shell could be "
                "located, so it was not run; re-run it without `repowise distill`"
            )
        argv: str | list[str] = [shell_exe, "-c", command_str]
        use_shell = False
    else:
        # shell=True on purpose: the user's own command may be a shell builtin
        # or a .cmd shim (npm on Windows); we execute exactly what they typed.
        argv, use_shell = command_str, True
    proc = subprocess.run(
        argv,
        shell=use_shell,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = proc.stdout
    if proc.stderr:
        output = output + ("\n" if output and not output.endswith("\n") else "") + proc.stderr

    text = _distill_or_raw(output, command_str, proc.returncode, source)
    _echo_safely(text)
    sys.exit(proc.returncode)


def _posix_shell() -> str | None:
    """Absolute path to the POSIX shell that launched this process, or None.

    Read out of the environment rather than looked up on ``PATH``, and that is
    the whole of the design. ``PATH`` on Windows answers ``bash`` with
    System32's ``bash.exe`` -- WSL, which is a different filesystem with a
    different ``git`` and no idea what a ``C:`` path means -- often before it
    answers with Git Bash. MSYS translates ``SHELL`` to a real Windows path
    for its children, so a command that arrived from the agent's Bash tool is
    handed back to the very interpreter that started us.

    None when nothing in the environment names a POSIX shell that exists. The
    caller must refuse rather than substitute one: the whole point is that the
    command means something different in the wrong shell.
    """
    shell = os.environ.get("SHELL")
    if shell and os.path.isfile(shell):
        return shell
    return None


# cmd.exe consumes these before the child ever sees them. ``^`` escapes each
# one for exactly one round of parsing, which is all ``cmd /c`` does.
_CMD_METACHARS = frozenset('"&|<>^()')

# A ``%NAME%`` pair cmd.exe would expand. Percent expansion happens in a pass
# before caret handling, so there is nothing to escape it with.
_CMD_VAR_RE = re.compile(r"%(\w+)%")


class UnrenderableCommandError(Exception):
    """A token cmd.exe cannot be handed over without changing the command."""


def _render_command(tokens: tuple[str, ...], *, posix: bool) -> str:
    """Rejoin click's pre-split tokens into one shell command string.

    *posix* selects the quoting dialect, and it is the dialect of the shell
    that will run the result rather than the dialect of this host: a command
    the agent wrote for bash is rejoined for bash even on Windows.

    A single token is passed through untouched: the user quoted the whole
    command themselves, so shell syntax in it is what they asked for.

    Multiple tokens are an argv the shell must not reinterpret.
    ``list2cmdline`` alone is not enough on Windows: it quotes for the C
    runtime's argv parser, which only cares about spaces and quotes, so a
    token like ``--grep=a&whoami`` comes back unquoted and cmd.exe reads the
    ``&`` as a command separator.

    So the arguments are caret-escaped — cmd strips one layer and hands the
    literal text to the child, and because every ``"`` is escaped too, cmd
    never enters a quoted state where the carets would stop working. The
    executable is the exception: it keeps real grouping quotes, because cmd
    resolves the program name before caret processing and a caret-escaped
    quote is not a grouping quote, which would split ``C:\\Program
    Files\\...`` at its first space.

    Two shapes cannot be rendered at all and raise rather than run something
    the user did not type. Both are rejected, not escaped, because cmd offers
    no escape for either:

    - ``%NAME%`` naming a variable that exists. cmd substitutes it and then
      re-parses the result, so a value carrying a metacharacter is command
      execution, not just substitution. Undefined names pass through
      untouched, which is what keeps ``git log --format=%h%n%s`` working.
    - A newline or carriage return. cmd truncates the command line at a
      newline and silently drops a bare CR, so the child would receive a
      quietly different argv.
    """
    if len(tokens) == 1:
        return tokens[0]
    if posix:
        import shlex

        return shlex.join(tokens)

    for token in tokens:
        if "\n" in token or "\r" in token:
            raise UnrenderableCommandError(
                "cmd.exe cannot carry a newline inside an argument; "
                "quote the whole command as one argument to run it verbatim"
            )
        for name in _CMD_VAR_RE.findall(token):
            if name in os.environ:
                raise UnrenderableCommandError(
                    f"cmd.exe would expand %{name}% and change this command; "
                    "quote the whole command as one argument to run it verbatim"
                )

    exe = tokens[0]
    # Windows paths cannot contain `"`, so plain quoting is unambiguous.
    if '"' not in exe and (
        any(ch.isspace() for ch in exe) or any(ch in _CMD_METACHARS for ch in exe)
    ):
        head = f'"{exe}"'
    else:
        head = _caret_escape(subprocess.list2cmdline([exe]))
    rest = _caret_escape(subprocess.list2cmdline(tokens[1:]))
    return f"{head} {rest}" if rest else head


def _caret_escape(rendered: str) -> str:
    return "".join("^" + ch if ch in _CMD_METACHARS else ch for ch in rendered)


def _distill_or_raw(output: str, command_str: str, exit_code: int, source: str = "cli") -> str:
    """Distill *output*, honoring the repo's ``distill:`` config block."""
    try:
        repo_root = find_repowise_repo_root()
        enabled, disabled, (ttl_days, max_mb) = _load_distill_config(repo_root)
        if not enabled:
            return output
        from repowise.core.distill import distill_output
        from repowise.core.distill.store import OmissionStore, default_store_path

        # Open the store here (rather than letting the engine) so the
        # configured TTL / size cap apply to this write's opportunistic prune.
        store = OmissionStore(
            default_store_path(repo_root or Path.cwd()), ttl_days=ttl_days, max_mb=max_mb
        )
        try:
            return distill_output(
                output,
                command=command_str,
                exit_code=exit_code,
                source=source,
                store=store,
                disabled_filters=disabled,
            ).text
        finally:
            store.close()
    except Exception:
        # The wrapped command already ran; never let distillation lose it.
        return output


def _load_distill_config(
    repo_root: Path | None,
) -> tuple[bool, tuple[str, ...], tuple[float, float]]:
    from repowise.core.distill.config import omission_store_settings

    if repo_root is None:
        return True, (), omission_store_settings(None)
    from repowise.core.repo_config import load_repo_config

    cfg = load_repo_config(repo_root).get("distill") or {}
    if not isinstance(cfg, dict):
        return True, (), omission_store_settings(None)
    enabled = bool(cfg.get("enabled", True))
    commands_cfg = cfg.get("commands") or {}
    raw_disabled = commands_cfg.get("disabled_filters") if isinstance(commands_cfg, dict) else None
    disabled = tuple(str(name) for name in raw_disabled) if isinstance(raw_disabled, list) else ()
    return enabled, disabled, omission_store_settings(cfg)


def _echo_safely(text: str) -> None:
    """Echo *text* without dying on non-UTF-8 consoles (Windows cp1252)."""
    try:
        click.echo(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
