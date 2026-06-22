"""The root Click group, instrumented for anonymous telemetry.

A single seam wraps every CLI invocation: it shows the one-time opt-out notice,
times the command, classifies the outcome, and records exactly one
``command_run`` event. Doing it here (rather than in each command) guarantees
uniform, complete coverage and keeps telemetry out of business logic.

Privacy: only the command name, a known subcommand name, option *names*, the
outcome, and a duration are captured. Positional arguments (which may be file
paths) are never recorded — a subcommand is only logged when it matches a real
registered subcommand of a group.
"""

from __future__ import annotations

import re
import sys
import time

import click

#: A well-formed long option name, e.g. ``--provider`` or ``--no-cost-tracking``.
_LONG_OPT = re.compile(r"--[A-Za-z][\w-]*")


def _option_name(token: str) -> str:
    """Reduce a CLI token to a bare option *name*, never a value.

    Critical for privacy: option *values* (which can be file paths or exclude
    globs) must never be recorded. This handles every shape Click accepts:

    * ``--name=value``      -> ``--name``
    * ``--name``            -> ``--name``
    * ``-xVALUE`` (attached) -> ``-x``   (drops the attached short-option value)
    * ``-x`` / ``-vv``      -> ``-x`` / ``-v``
    * anything malformed     -> the leading dash run only
    """
    if token.startswith("--"):
        head = token.split("=", 1)[0]
        m = _LONG_OPT.fullmatch(head)
        return m.group(0) if m else "--"
    # Single-dash: keep only the dash and the first option letter, dropping any
    # attached value (``-p/secret/path`` -> ``-p``) or extra combined letters.
    return token[:2]


class InstrumentedGroup(click.Group):
    """Root group that emits one telemetry event per invocation."""

    def invoke(self, ctx: click.Context):
        from repowise.cli.platform import telemetry

        telemetry.maybe_show_notice()

        # The unparsed tail (subcommand + its args) is captured now, before
        # ``super().invoke`` may consume it.
        tail = list(ctx.args)

        start = time.monotonic()
        status = "ok"
        error_type: str | None = None
        try:
            return super().invoke(ctx)
        except SystemExit as exc:
            if exc.code not in (0, None):
                status = "error"
            raise
        except (click.ClickException, click.exceptions.Abort) as exc:
            status = "error"
            error_type = type(exc).__name__  # class name only, never the message
            raise
        except Exception as exc:
            status = "error"
            error_type = type(exc).__name__
            raise
        finally:
            # ``invoked_subcommand`` is only populated once ``super().invoke``
            # has parsed and dispatched, so it is read here, not before. It is
            # ``None`` for bare ``--help``/``--version`` (nothing to record).
            command = ctx.invoked_subcommand
            if command and command != "*":
                duration_ms = int((time.monotonic() - start) * 1000)
                subcommand, flags = self._subcommand_and_flags(ctx, command, tail)
                telemetry.record_command_run(
                    command=command,
                    subcommand=subcommand,
                    flags=flags,
                    status=status,
                    error_type=error_type,
                    duration_ms=duration_ms,
                )

    def _subcommand_and_flags(
        self, ctx: click.Context, command: str, tail: list[str]
    ) -> tuple[str | None, list[str]]:
        """Extract a safe subcommand name and the option *names* of this run.

        Flags come from ``sys.argv`` reduced to bare option names by
        :func:`_option_name`, so no value (incl. attached short-option values
        like ``-p/path``) is ever recorded. The subcommand is taken from the
        unparsed tail and only returned when it matches a real registered
        subcommand of a group — so positional arguments (which may be file
        paths) are never logged.
        """
        flags = [_option_name(tok) for tok in sys.argv[1:] if tok.startswith("-")]

        subcommand: str | None = None
        try:
            cmd = self.get_command(ctx, command)
            if isinstance(cmd, click.MultiCommand):
                known = set(cmd.list_commands(ctx))
                for tok in tail:
                    if tok.startswith("-"):
                        continue
                    if tok in known:
                        subcommand = tok
                    break
        except Exception:
            pass
        return subcommand, flags
