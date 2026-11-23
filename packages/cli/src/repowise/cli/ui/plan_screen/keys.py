"""Reading one key from the plan screen."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

import click.termui
from rich.console import Console
from rich.prompt import Prompt

from repowise.cli.ui.plan_screen.state import PlanState

START, MODEL, EDIT, THIS_REPO, QUIT = "start", "model", "edit", "this_repo", "quit"
#: Workspace only: choose the repos and the primary.
REPOS = "repos"



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
    console: Console,
    state: PlanState,
    ask: Callable[[], str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> str:
    """The action for the next key: start, model, edit, this_repo or quit.

    *extra* maps further keys to actions, offered on this screen only.

    Line-based on purpose: a single-keypress read goes to the console device
    on Windows, so it would block an agent whose stdin is a pipe or
    ``/dev/null``. Rich's prompt, not click's: click turns EOF into Abort,
    which is also Ctrl-C. EOFError propagates; the caller treats it as
    "nobody can answer".
    """
    if ask is None:

        def ask() -> str:
            return Prompt.ask("  Choice", default="", show_default=False, console=console)

    keys = {"": START, "m": MODEL, "e": EDIT, **(extra or {})}
    if state.can_keep_global:
        keys["s"] = THIS_REPO
    keys["q"] = QUIT
    offered = [k for k in keys if k]
    hint = ", ".join(offered[:-1]) + f" or {offered[-1]}"
    while True:
        action = keys.get(ask().strip().lower())
        if action is not None:
            return action
        console.print(f"  [dim]Press Enter to start, or {hint}.[/dim]")
