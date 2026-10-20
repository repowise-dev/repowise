"""Reading one key from the plan screen."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager

import click.termui
from rich.console import Console
from rich.prompt import Prompt

from repowise.cli.ui.plan_screen.state import PlanState

START, MODEL, EDIT, THIS_REPO, QUIT = "start", "model", "edit", "this_repo", "quit"

_KEYS = {"": START, "m": MODEL, "e": EDIT, "s": THIS_REPO, "q": QUIT}


class _InputEndedError(Exception):
    """End of input inside a click prompt; click lets anything but EOF and ^C through."""


@contextmanager
def end_of_input_is_eof() -> Iterator[None]:
    """Inside, end of input in a click prompt raises EOFError, the way Rich's does.

    Click turns EOF into Abort, the same exception as Ctrl-C, so the
    questions behind ``m`` and ``e`` would end the run where the key read
    falls back to defaults. Ctrl-C is untouched and still aborts.
    """
    saved = click.termui.visible_prompt_func, click.termui.hidden_prompt_func

    def ending(read: Callable[[str], str]) -> Callable[[str], str]:
        def guarded(prompt: str) -> str:
            try:
                return read(prompt)
            except EOFError:
                raise _InputEndedError from None

        return guarded

    click.termui.visible_prompt_func = ending(saved[0])
    click.termui.hidden_prompt_func = ending(saved[1])
    try:
        yield
    except _InputEndedError:
        raise EOFError from None
    finally:
        click.termui.visible_prompt_func, click.termui.hidden_prompt_func = saved


def read_plan_key(
    console: Console, state: PlanState, ask: Callable[[], str] | None = None
) -> str:
    """The action for the next key: start, model, edit, this_repo or quit.

    Line-based on purpose: a single-keypress read goes to the console device
    on Windows, so it would block an agent whose stdin is a pipe or
    ``/dev/null``. Rich's prompt, not click's: click turns EOF into Abort,
    which is also Ctrl-C. EOFError propagates; the caller treats it as
    "nobody can answer".
    """
    if ask is None:

        def ask() -> str:
            return Prompt.ask("  Choice", default="", show_default=False, console=console)

    while True:
        raw = ask().strip().lower()
        action = _KEYS.get(raw)
        if action == THIS_REPO and not state.can_keep_global:
            action = None
        if action is not None:
            return action
        offered = "m, e, s or q" if state.can_keep_global else "m, e or q"
        console.print(f"  [dim]Press Enter to start, or {offered}.[/dim]")
