"""Standalone entry point for the Claude Code ``repowise-augment`` hook.

Hooks must never crash the agent. The full ``repowise`` CLI imports the
entire command surface — including ``init_cmd`` → ``cost_estimator`` →
``core.ingestion.graph``, which pulls in ``networkx``, ``scipy``, and other
heavy dependencies. A single missing dep (or any other import-time failure
in any subcommand) would otherwise propagate as a non-zero exit on every
``Grep``/``Glob``/``Bash`` tool call, spamming the agent transcript with
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

import sys


def main() -> None:
    try:
        from repowise.cli.commands.augment_cmd import _run_augment

        _run_augment()
    except (SystemExit, KeyboardInterrupt):
        raise
    except BaseException:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
