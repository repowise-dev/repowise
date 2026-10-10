"""The one sentence that says where things stand, per horizon.

Composed here so the CLI, the web app and every other surface print the same
words; they render ``view["summary"][horizon]`` rather than rebuilding it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def work(horizon: dict[str, Any]) -> int:
    """``act_now`` plus ``plan``: the part of a horizon that is real work."""
    by_tier = horizon.get("by_tier", {})
    return int(by_tier.get("act_now", 0)) + int(by_tier.get("plan", 0))


def plural(n: int, noun: str) -> str:
    return f"{n:,} {noun}{'' if n == 1 else 's'}"


def _sentence(horizons: dict[str, Any], name: str, anchor: datetime | None) -> str:
    # The window is named because "this week" is the repository's last week of
    # commits, which is not always the calendar's.
    if name == "week":
        until = f"{anchor:%b} {anchor.day}" if anchor else None
        window = f"in the week to {until}, the last indexed commit" if until else "this week"
    else:
        window = "this quarter"
    total = work(horizons[name])
    if total == 0:
        other = work(horizons["quarter" if name == "week" else "week"])
        if other == 0:
            return f"Nothing stands out {window}."
        if name == "week":
            tail = f"{'is' if other == 1 else 'are'} worth planning this quarter"
        else:
            tail = "came up this week"
        return f"Nothing needs you {window}. {plural(other, 'thing')} {tail}."
    now = int(horizons[name].get("by_tier", {}).get("act_now", 0))
    now_part = f", {now} of them now" if now else ""
    return f"{plural(total, 'thing')} worth doing {window}{now_part}."


def summarize(horizons: dict[str, Any], anchor: datetime | None) -> dict[str, str]:
    """``{horizon: sentence}`` over the ranked horizons ``compose_actions`` built."""
    return {name: _sentence(horizons, name, anchor) for name in horizons}
