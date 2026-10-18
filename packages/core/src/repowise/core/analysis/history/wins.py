"""Files whose health score rose over a window, from two stored snapshots.

A snapshot holds every scored file's score, so comparing the one taken at the
start of a window with the newest names the files that got better. A score
also moves when the analyzer is re-tuned, which is not anyone's work, so a
caller passes the paths commits actually touched in the window and only those
count.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass

#: Smallest rise worth naming: below it a score moved by rounding, not by work.
MIN_GAIN = 0.1


@dataclass(frozen=True, slots=True)
class FileGain:
    path: str
    before: float
    after: float

    @property
    def gain(self) -> float:
        return round(self.after - self.before, 2)


def parse_score_map(raw: str | None) -> dict[str, float]:
    """A snapshot's ``{path: score}`` JSON; empty when absent or not an object."""
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, float] = {}
    for path, score in parsed.items():
        try:
            out[path] = float(score)
        except (TypeError, ValueError):
            continue
    return out


def rising_files(
    before: Mapping[str, float],
    after: Mapping[str, float],
    touched: Collection[str],
    *,
    min_gain: float = MIN_GAIN,
) -> list[FileGain]:
    """Touched files scored in both maps whose score rose by *min_gain* or more,
    biggest gain first (path breaks ties)."""
    gains = [
        FileGain(path, before[path], after[path])
        for path in touched
        if path in before and path in after and after[path] - before[path] >= min_gain
    ]
    gains.sort(key=lambda g: (-g.gain, g.path))
    return gains
