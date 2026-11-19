"""Run a command a user typed: the one owner for rendering and running it.

``repowise distill`` and ``repowise perf measure`` both run a command the
caller typed after their own options. Rendering click's pre-split tokens back
into one shell command line, and running that line with a timeout, a working
directory and environment overrides, live here so the two never disagree about
what a command means on Windows.

Only a command the caller typed is ever run. Nothing here builds one.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

# cmd.exe consumes these before the child ever sees them. ``^`` escapes each
# one for exactly one round of parsing, which is all ``cmd /c`` does.
_CMD_METACHARS = frozenset('"&|<>^()')

# A ``%NAME%`` pair cmd.exe would expand. Percent expansion happens in a pass
# before caret handling, so there is nothing to escape it with.
_CMD_VAR_RE = re.compile(r"%(\w+)%")

#: Bytes of combined output kept for a failure message. A failing run's last
#: lines name the error; the rest is noise the caller does not need.
_TAIL_BYTES = 8192


class UnrenderableCommandError(Exception):
    """A token cmd.exe cannot be handed over without changing the command."""


def render_command(tokens: tuple[str, ...]) -> str:
    """Rejoin click's pre-split tokens into one shell command string.

    A single token is passed through untouched: the user quoted the whole
    command themselves, so shell syntax in it is what they asked for.

    Multiple tokens are an argv the shell must not reinterpret.
    ``list2cmdline`` alone is not enough on Windows: it quotes for the C
    runtime's argv parser, which only cares about spaces and quotes, so a
    token like ``--grep=a&whoami`` comes back unquoted and cmd.exe reads the
    ``&`` as a command separator.

    So the arguments are caret-escaped: cmd strips one layer and hands the
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
    if sys.platform != "win32":
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
    # Windows paths cannot contain `"`, so plain quoting is unambiguous. A `/`
    # is quoted too: unquoted, cmd reads `./.venv/Scripts/python.exe` (the
    # spelling a Git Bash command arrives in) as the program `.` and a switch.
    if '"' not in exe and (
        any(ch.isspace() for ch in exe) or any(ch in _CMD_METACHARS or ch == "/" for ch in exe)
    ):
        head = f'"{exe}"'
    else:
        head = _caret_escape(subprocess.list2cmdline([exe]))
    rest = _caret_escape(subprocess.list2cmdline(tokens[1:]))
    return f"{head} {rest}" if rest else head


def _caret_escape(rendered: str) -> str:
    return "".join("^" + ch if ch in _CMD_METACHARS else ch for ch in rendered)


@dataclass(frozen=True, slots=True)
class CommandRun:
    """One run of a command: how it ended and how long it took."""

    #: The process exit code; ``None`` when the run was killed on timeout.
    exit_code: int | None
    #: Wall time from spawn to exit, in seconds.
    seconds: float
    timed_out: bool
    #: The last lines of combined stdout and stderr, for a failure message.
    output_tail: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_command(
    command: str,
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    tail_lines: int = 20,
    clock: Callable[[], float] = time.perf_counter,
) -> CommandRun:
    """Run *command* through the shell once and time it.

    ``shell=True`` on purpose, as in ``distill``: the command may be a shell
    builtin or a ``.cmd`` shim (npm on Windows), and it runs exactly as typed.
    *env* entries override the inherited environment rather than replace it.
    On *timeout* the whole process tree is killed, not just the shell, so a
    child holding the output open cannot keep the run alive.

    Output goes to a temporary file rather than a pipe: nothing has to drain
    it while the command runs, and a grandchild that outlives a killed shell
    cannot block the read.

    Only the last :data:`_TAIL_BYTES` are read back, decoded as UTF-8. The
    file itself is not capped: a command that prints gigabytes writes them to
    the temp directory. Cap it here if that ever bites.
    """
    full_env = None if env is None else {**os.environ, **env}
    with tempfile.TemporaryFile() as out:
        start = clock()
        # shell=True by design: the caller typed this exact command line and
        # it runs as typed (builtins, .cmd shims). Nothing here builds one.
        proc = subprocess.Popen(  # repowise-security-ignore: subprocess_shell_true
            command,
            shell=True,
            cwd=str(cwd) if cwd is not None else None,
            env=full_env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            # Its own process group on POSIX, so a timeout kills the shell's
            # children too.
            start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            exit_code: int | None = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            proc.wait()
            exit_code, timed_out = None, True
        except BaseException:
            # Ctrl-C or any other interruption must not leave the run behind.
            _kill_tree(proc)
            proc.wait()
            raise
        seconds = clock() - start
        out.seek(0, os.SEEK_END)
        out.seek(max(0, out.tell() - _TAIL_BYTES))
        text = out.read().decode("utf-8", errors="replace")
    tail = "\n".join(text.splitlines()[-tail_lines:]) if tail_lines > 0 else ""
    return CommandRun(exit_code, seconds, timed_out, tail)


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        # taskkill /T takes the children cmd.exe started; Popen.kill would
        # only end cmd.exe and leave them running.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.poll() is None:
            proc.kill()
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


__all__ = [
    "CommandRun",
    "UnrenderableCommandError",
    "render_command",
    "run_command",
]
