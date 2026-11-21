"""``repowise distill`` — run a command and emit a distilled rendering.

Executes the real command, captures its output, and prints the compact
errors-first rendering with an omission marker pointing at the stashed raw
output (``repowise expand <ref>`` round-trips it). The wrapped command's
exit code is always preserved, so this is a drop-in replacement in scripts
and agent tool calls alike.

The wrapped command runs in the shell it was *written* for, not the one this
host happens to default to. ``--source hook-bash`` names a POSIX command
line, so on Windows a command carrying POSIX syntax of its own executes
through the agent's Git Bash rather than ``cmd.exe``, where quoting means
something else and ``&&`` binds differently. That is the constraint the
rewrite hook used to defend by declining to rewrite anything with a
metacharacter on Windows.

The dialect rides on ``--source`` rather than a flag of its own so that an
older ``repowise distill`` on PATH still understands every command a newer
hook writes. A flag it did not know would land at the front of the wrapped
command and be run as the program.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click

from repowise.cli._setup import configure_cli_logging
from repowise.cli.agent_adapters.base import SHELL_POSIX, dialect_for_hook_source
from repowise.cli.helpers import find_repowise_repo_root
from repowise.core.command_runner import UnrenderableCommandError, render_command


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
    help=(
        "Ledger surface label (the rewrite hook tags hook-bash / "
        "hook-powershell). Load-bearing beyond the ledger: 'hook-bash' names a "
        "POSIX command line, so on Windows a command carrying POSIX shell "
        "syntax is run by a POSIX shell rather than cmd.exe."
    ),
)
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
def distill_command(source: str, command: tuple[str, ...]) -> None:
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
    # Only a command handed over as ONE token carries shell syntax of its
    # own — that is the shape the hook wraps a chain or a pipeline in, and the
    # shape a user quotes deliberately. A multi-token argv is the same command
    # in either dialect, and rendering it for this host is what has always
    # worked, so the POSIX shell — and the refusal when there is none — stays
    # confined to exactly the commands that need it.
    needs_posix_shell = (
        dialect_for_hook_source(source) == SHELL_POSIX
        and sys.platform == "win32"
        and len(command) == 1
    )
    try:
        command_str = render_command(command)
    except UnrenderableCommandError as exc:
        # Refusing is the safe half of the trade: running a command the user
        # did not type is worse than not running one they did.
        raise click.ClickException(str(exc)) from exc
    if needs_posix_shell:
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


#: Shells that read a POSIX command line from ``-c``. An allowlist rather
#: than a "does this file exist" test, because the wrong answer here is not an
#: error — it is a different command running and reporting success.
_POSIX_SHELL_NAMES = frozenset({"bash", "sh"})


def _posix_shell() -> str | None:
    """Absolute path to the POSIX shell that launched this process, or None.

    Read out of the environment rather than looked up on ``PATH``, and that is
    the whole of the design. ``PATH`` on Windows answers ``bash`` with
    System32's ``bash.exe`` -- WSL, which is a different filesystem with a
    different ``git`` and no idea what a ``C:`` path means -- often before it
    answers with Git Bash. MSYS translates ``SHELL`` to a real Windows path
    for its children, so a command that arrived from the agent's Bash tool is
    handed back to the very interpreter that started us.

    Existence is not the test. ``-c`` is also how ``powershell.exe`` takes a
    command, so the name has to be one of ``_POSIX_SHELL_NAMES``, and WSL's
    ``System32\\bash.exe`` is excluded by path even though it passes that.

    None when nothing in the environment names a POSIX shell this process can
    use. The caller must refuse rather than substitute one: the whole point is
    that the command means something different in the wrong shell.
    """
    shell = os.environ.get("SHELL")
    if not shell or not os.path.isfile(shell):
        return None
    name = os.path.basename(shell).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name not in _POSIX_SHELL_NAMES:
        # ``-c`` is also how powershell.exe and pwsh.exe take a command, so an
        # existence check alone would hand a POSIX command line to PowerShell
        # and get a plausible answer to a different question.
        return None
    if _under_system32(shell):
        # System32's ``bash.exe`` is WSL: another filesystem, another ``git``,
        # and no reading of a ``C:`` path that matches this process's. It is a
        # POSIX shell and still the wrong one.
        return None
    return shell


def _under_system32(path: str) -> bool:
    # os.environ is case-insensitive on Windows, which is the only host
    # this is reached on.
    root = os.environ.get("SYSTEMROOT")
    if not root:
        return False
    try:
        prefix = os.path.normcase(os.path.join(os.path.abspath(root), "system32")) + os.sep
        return os.path.normcase(os.path.abspath(path)).startswith(prefix)
    except (OSError, ValueError):
        return False


def _distill_or_raw(output: str, command_str: str, exit_code: int, source: str = "cli") -> str:
    """Distill *output*, honoring the repo's ``distill:`` config block."""
    # The engine logs a filter that falls back to raw at debug level. Left
    # unconfigured, structlog prints that line into the output the agent reads.
    configure_cli_logging(verbose=False)
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
    from repowise.core.settings.model import flag

    enabled = flag(cfg.get("enabled"), True)
    commands_cfg = cfg.get("commands") or {}
    raw_disabled = commands_cfg.get("disabled_filters") if isinstance(commands_cfg, dict) else None
    if isinstance(raw_disabled, str):
        raw_disabled = [raw_disabled]  # one bare name is a list of one
    disabled = tuple(str(name) for name in raw_disabled) if isinstance(raw_disabled, list) else ()
    return enabled, disabled, omission_store_settings(cfg)


def _echo_safely(text: str) -> None:
    """Echo *text* without dying on non-UTF-8 consoles (Windows cp1252)."""
    try:
        click.echo(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
