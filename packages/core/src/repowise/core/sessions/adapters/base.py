"""The adapter contract every harness implements.

An adapter knows two things about its agent: where transcripts for a given
repo live (:meth:`HarnessAdapter.discover`) and how one raw transcript line
becomes a normalized :class:`~repowise.core.sessions.events.Event`
(:meth:`HarnessAdapter.normalize`). Iteration, cursoring, and mining are
shared code built on those two primitives.

Best-effort contract, matching the distill miners this layer was extracted
from: ``normalize`` returns None for anything it cannot parse rather than
raising; filesystem errors from :meth:`iter_events` propagate so callers can
apply their own skip policy per file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import ClassVar

from repowise.core.sessions.events import Event

#: Cheap gate on the raw line, applied before paying for JSON parsing.
#: Transcript lines routinely run to hundreds of kilobytes; a substring
#: check on the raw string is orders of magnitude cheaper than json.loads,
#: so consumers that only want tool events pass one of these.
RawPrefilter = Callable[[str], bool]


class HarnessAdapter(ABC):
    """Reads one agent's transcripts into the shared Event stream."""

    #: Stable identifier, e.g. ``"claude_code"``.
    name: ClassVar[str]

    @abstractmethod
    def discover(self, repo_root: Path, *, projects_root: Path | None = None) -> list[Path]:
        """Transcript files for sessions rooted at *repo_root*, sorted.

        *projects_root* overrides the harness's real transcript root (for
        tests). An absent directory yields an empty list, never an error.
        """

    @abstractmethod
    def normalize(self, raw_line: str) -> Event | None:
        """One raw transcript line as an Event, or None when unparseable."""

    def iter_events(self, path: Path, *, prefilter: RawPrefilter | None = None) -> Iterator[Event]:
        """Events from one transcript file, in order.

        Lines failing *prefilter* are skipped without being parsed. Decoding
        is tolerant (``errors="replace"``); ``OSError`` propagates.
        """
        with path.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                if prefilter is not None and not prefilter(raw):
                    continue
                event = self.normalize(raw)
                if event is not None:
                    yield event
