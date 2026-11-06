"""Standalone entry point for installed AI-agent ``repowise-augment`` hooks.

Hooks must never crash the agent. The full ``repowise`` CLI imports the
entire command surface — including ``init_cmd`` → ``cost_estimator`` →
``core.ingestion.graph``, which pulls in ``networkx``, ``scipy``, and other
heavy dependencies. A single missing dep (or any other import-time failure
in any subcommand) would otherwise propagate as a non-zero exit on every
``Grep``/``Glob``/``Bash``/edit tool call, spamming the agent transcript with
tracebacks.

This entry point is wired as a separate ``[project.scripts]`` console script
so:

  - It does not transitively import any subcommand modules — only the
    handler in ``commands.augment_cmd``, which itself only imports
    ``json``/``sys``/``click`` at module scope (heavy queries are lazy).
  - The entire body, including the import, is wrapped in a last-ditch
    ``except BaseException`` so any failure (broken venv, corrupt DB,
    even ``ImportError``) exits 0 silently with no output.

The Click command ``repowise augment`` still works for manual debugging;
hook installers should write ``repowise-augment`` instead.
"""

from __future__ import annotations

import contextlib
import sys


def _parse_client_arg(argv: list[str]) -> str | None:
    for idx, arg in enumerate(argv):
        if arg == "--client" and idx + 1 < len(argv):
            return argv[idx + 1]
        if arg.startswith("--client="):
            return arg.split("=", 1)[1]
    return None


def main() -> None:
    # stdout is the agent's JSON channel: only the hook response goes there.
    # Anything else that prints (structlog's default logger) goes to stderr.
    response_stream = sys.stdout
    with contextlib.redirect_stdout(sys.stderr):
        try:
            if "--health-gate" in sys.argv[1:]:
                # The opt-in PreToolUse gate: its own stdlib-only module, so a
                # shell call that is not a commit or push pays no augment import.
                from repowise.cli.health_gate_hook import run

                run(sys.stdin, response_stream)
            else:
                from repowise.cli.commands.augment_cmd import _run_augment, command

                command._response_stream = response_stream
                try:
                    _run_augment(
                        client=_parse_client_arg(sys.argv[1:]),
                        coverage_only="--coverage-only" in sys.argv,
                    )
                finally:
                    command._response_stream = None
        except (SystemExit, KeyboardInterrupt):
            raise
        except BaseException:
            pass

        # Best-effort self-heal for users whose only repowise invocation is
        # through this hook. Wrapped so a write failure never propagates back to
        # the agent.
        #
        # This is the fire that runs once per matched tool call, so it is where the
        # version stamp pays for itself: it used to parse ~/.claude/settings.json
        # every time to discover, almost always, that there was nothing to do.
        try:
            from repowise.cli.self_heal import run_editor_migrations

            run_editor_migrations()
        except BaseException:
            pass

    sys.exit(0)


if __name__ == "__main__":
    main()
