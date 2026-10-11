"""How much of a function's work is talking to a terminal.

A status report, a doctor checklist or a setup wizard is long and branchy by
construction: each check prints a line, each step prompts. Its CCN counts
``if configured: print(...) else: print(...)`` pairs, which split into helpers
without making anything safer. ``terminal_share`` lets a reader tell that shape
apart from a function whose branches decide what the program does.

The share is the calls in the function (nested bodies included) that write to
or read from a terminal, over all its calls, read by the callee's name: the
print family in any language, a ``print_*`` / ``prompt_*`` / ``confirm_*``
helper, ``console.log`` and its siblings, and a write to stdout or stderr. A
function with fewer than :data:`MIN_CALLS` calls has no share.
"""

from __future__ import annotations

#: From this share of its calls on the terminal, a function is a print or
#: prompt flow. Chosen in the gap between the status and setup commands
#: (0.27 to 0.50) and the largest share of a function that decides program
#: behaviour (0.15, a CLI init command) in three repositories' Fix first lists.
TERMINAL_SHARE = 0.25
#: Fewer calls than this say nothing about what a function is for.
MIN_CALLS = 4

_PLAIN = frozenset(
    {
        "print",
        "println",
        "printf",
        "eprint",
        "eprintln",
        "puts",
        "echo",
        "secho",
        "pprint",
        "input",
        "getpass",
        "prompt",
        "confirm",
    }
)
# Prefixes with their separator: ``printer_for`` and ``echo_server`` are not output.
_PREFIXES = ("print_", "prompt_", "confirm_")
_CONSOLE_METHODS = frozenset({"log", "info", "warn", "error", "table", "debug"})
_STREAMS = frozenset({"stdout", "stderr"})


def is_terminal_call(name: str, roots: set[str]) -> bool:
    """Whether a call named *name* (lowercased) on receiver path *roots*
    writes to or reads from a terminal."""
    bare = name.lstrip("_")
    if bare in _PLAIN or bare.startswith(_PREFIXES):
        return True
    if "console" in roots and bare in _CONSOLE_METHODS:
        return True
    return bare == "write" and not _STREAMS.isdisjoint(roots)


def terminal_share(terminal: int, calls: int) -> float:
    """*terminal* calls over *calls*, to two decimals; 0.0 under :data:`MIN_CALLS`."""
    return round(terminal / calls, 2) if calls >= MIN_CALLS else 0.0


__all__ = ["MIN_CALLS", "TERMINAL_SHARE", "is_terminal_call", "terminal_share"]
