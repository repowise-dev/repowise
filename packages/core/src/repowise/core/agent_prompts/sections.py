"""The section helpers every agent prompt is written with.

Written to match ``packages/ui/src/health/ai-prompts/shared.ts``, which the web's
own prompt builders use; the goldens in ``tests/fixtures/agent_prompts`` pin
core's output.
"""

from __future__ import annotations

from collections.abc import Iterable


def count(n: int) -> str:
    """``1,234``: the grouping ``toLocaleString()`` gives under an en-US locale.

    Ceiling: a browser under another locale groups differently (en-IN writes
    ``1,23,456``); the goldens stay below 100,000, where the two agree.
    """
    return f"{n:,}"


def bullet_list(items: Iterable[str | None]) -> str:
    return "\n".join(f"- {s}" for s in items if s)


def repo_suffix(repo_name: str | None) -> str:
    """The `` (`repo`)`` suffix a prompt heading carries when the host named the repo."""
    return f" (`{repo_name}`)" if repo_name else ""


def and_more(n: int, *, prefix: str = "", suffix: str = "") -> str:
    """``…and N more`` for a capped list, or "" when nothing was cut."""
    return f"{prefix}…and {count(n)} more{suffix}" if n > 0 else ""


def closing_sections(constraints: Iterable[str | None], expected: Iterable[str]) -> list[str]:
    """The constraints and expected-output sections most prompts end on."""
    return [
        "## Hard constraints",
        "",
        bullet_list(constraints),
        "",
        "## What I expect back",
        "",
        "\n".join(expected),
        "",
    ]


def join_sections(sections: Iterable[str]) -> str:
    """Join sections, dropping every "" (absent sections and bare separators
    alike), so a section that needs a blank line before it carries its own newline."""
    return "\n".join(s for s in sections if s != "")
