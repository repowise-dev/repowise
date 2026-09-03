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
    """Root group that emits one telemetry event per invocation.

    Also the lazy half of the CLI registry: a command can be registered
    as a ``"module:attr"`` string and is imported only when it is the one
    being run. Dispatching ``repowise status`` used to import all ~35
    command modules; now it imports one.

    ``--help`` still resolves everything, because Click needs each
    command's ``short_help`` to render the listing. That is deliberate:
    the alternative is a duplicated static help map that drifts, and
    ``--help`` is human-interactive while hooks, MCP and scripts never
    call it.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        #: name -> "module:attr", for commands not yet imported.
        self._lazy_commands: dict[str, str] = {}

    def add_lazy_command(self, name: str, target: str) -> None:
        """Register *name* without importing the module that defines it."""
        # Last registration wins, which is what ``click.Group.add_command``
        # does and therefore what plugin overrides have always relied on.
        # Without this drop, an already-resolved (or eagerly registered)
        # command of the same name would keep winning in ``get_command``,
        # silently inverting precedence between a plugin and the OSS CLI.
        self.commands.pop(name, None)
        self._lazy_commands[name] = target

    def add_command(self, cmd: click.Command, name: str | None = None) -> None:
        """Attach *cmd*, superseding any lazy registration of the same name."""
        super().add_command(cmd, name)
        self._lazy_commands.pop(name or cmd.name or "", None)

    def list_commands(self, ctx: click.Context) -> list[str]:
        """Every command name, resolved and unresolved, importing nothing."""
        return sorted({*super().list_commands(ctx), *self._lazy_commands})

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        """Resolve one command, importing its module on first use."""
        resolved = super().get_command(ctx, name)
        if resolved is not None:
            return resolved
        target = self._lazy_commands.get(name)
        if target is None:
            return None
        from repowise.core.registry import LazyCommand

        command = LazyCommand(name, target).load()
        # Cached on the group, so a second lookup in the same process
        # (``--help`` after dispatch, telemetry's tail match) is free.
        self.add_command(command, name)
        return command

    def invoke(self, ctx: click.Context):
        from repowise.cli.platform import telemetry

        # Not for ``uninstall``. Reading the consent state resolves
        # ``~/.repowise/``, and that resolution creates the directory even on a
        # pure read, so this ran before any command code and put back the
        # directory the previous ``uninstall --all`` had deleted. The command
        # then reported machine-wide state ``removed`` on every run, forever.
        #
        # Two other writes to the same directory had to be stopped for the same
        # reason (the spool on the way out, the self-heal stamp in ``main``).
        # This is the earliest of the three, which is why it survived them.
        # Read from the unparsed args, not from ``ctx.invoked_subcommand``:
        # Click sets that inside ``MultiCommand.invoke``, which is the call we
        # are wrapping, so up here it is still None for every command and the
        # guard silently never fired.
        pending = getattr(ctx, "protected_args", None) or ctx.args
        if not pending or pending[0] != "uninstall":
            telemetry.maybe_show_notice()

        # The unparsed tail (subcommand + its args) is captured now, before
        # ``super().invoke`` may consume it.
        tail = list(ctx.args)

        start = time.monotonic()
        status = "ok"
        error_type: str | None = None
        try:
            return super().invoke(ctx)
        except click.exceptions.Exit as exc:
            # click's ctx.exit(): code 0 is a clean success (must NOT read as an
            # error), 130 is a Ctrl-C exit, anything else is a real failure.
            code = getattr(exc, "exit_code", 0)
            if code in (0, None):
                status = "ok"
            elif code == 130:
                status = "interrupted"
            else:
                status = "error"
                error_type = "Exit"
            raise
        except SystemExit as exc:
            if exc.code in (0, None):
                status = "ok"
            elif exc.code == 130:
                status = "interrupted"
            else:
                status = "error"
                # Mirror the ``Exit`` branch above and name the class, or this
                # bucket records a failure with no error type at all. Commands
                # that raise ``SystemExit`` directly rather than via
                # ``ctx.exit()`` all land here, so leaving it unset loses the
                # only diagnostic the event carries. Prefer the chained cause
                # when there is one: a bare ``SystemExit(1)`` says nothing on
                # its own, whereas the exception that forced the exit does.
                # ``from None`` is honoured — a suppressed context is the
                # author saying it is not the explanation.
                cause = exc.__cause__
                if cause is None and not exc.__suppress_context__:
                    cause = exc.__context__
                error_type = type(cause).__name__ if cause else "SystemExit"
            raise
        except (KeyboardInterrupt, click.exceptions.Abort):
            # User cancelled (Ctrl-C / declined a prompt). Not a failure — long-
            # running commands (serve/watch/init) are routinely Ctrl-C'd, and
            # counting that as "error" made success rates uninterpretable.
            status = "interrupted"
            raise
        except click.UsageError as exc:
            # Bad/unknown option, missing or malformed argument: the user
            # mis-invoked the command (a typo, a wrong flag), not a product
            # failure. Kept out of the ``error`` bucket so the real crash rate
            # is readable — a UsageError subclasses ClickException, so this
            # branch must sit *before* the ClickException one below.
            status = "usage_error"
            error_type = type(exc).__name__  # class name only, never the message
            raise
        except click.ClickException as exc:
            status = "error"
            error_type = type(exc).__name__  # class name only, never the message
            # Only the exception that actually ends the command reports its
            # reason. Recording at the raise site instead attributed a failure
            # to runs that caught it and went on to succeed.
            reason = getattr(exc, "reason", None)
            if isinstance(reason, str) and reason:
                from repowise.cli.platform import telemetry

                telemetry.add_command_outcome(failure_reason=reason)
            raise
        except Exception as exc:
            status = "error"
            error_type = type(exc).__name__
            # A task-group crash raises one leaf and stamps the class names of
            # its siblings on it. `error_type` can only name the one, which
            # cannot say whether a crash-looping server has a single fault or
            # several. Read here rather than in the command, so every host of a
            # task group is covered and an interrupt - handled above, and not a
            # failure - never lands in the failure dimension.
            from repowise.core.platform.telemetry import GROUP_LEAF_TYPES_ATTR

            leaves = getattr(exc, GROUP_LEAF_TYPES_ATTR, None)
            if isinstance(leaves, tuple) and leaves:
                from repowise.cli.platform import telemetry

                telemetry.add_command_outcome(
                    error_leaves=",".join(leaves), error_leaf_count=len(leaves)
                )
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
                    extra=telemetry.drain_command_outcome(),
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
