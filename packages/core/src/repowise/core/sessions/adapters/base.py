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
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import ClassVar

from repowise.core.sessions.events import Event

#: Cheap gate on the raw line, applied before paying for JSON parsing.
#: Transcript lines routinely run to hundreds of kilobytes; a substring
#: check on the raw string is orders of magnitude cheaper than json.loads,
#: so consumers that only want tool events pass one of these.
RawPrefilter = Callable[[str], bool]

#: What a consumer wants out of a transcript, named rather than spelled.
#: A consumer asks for an intent; the adapter owns which of its harness's
#: key names spot it, so harness vocabulary never leaks into a miner.
#:
#: Shell tool calls together with their results (command-level mining).
INTENT_SHELL_CALLS = "shell_calls"
#: Any tool call or tool result, shell or otherwise.
INTENT_TOOL_CALLS = "tool_calls"
#: Conversation turns: user prose, assistant prose, and the tool traffic
#: carried on them.
INTENT_TURNS = "turns"


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

    def prefilter(self, intent: str) -> RawPrefilter | None:
        """A cheap raw-line gate for *intent*, or None for no gate.

        The returned predicate reads the **raw string** and runs before any
        parsing: lines routinely run to hundreds of kilobytes, and this gate
        is most of the layer's speed. An adapter that normalizes the line to
        decide would give all of it back, so overrides return a predicate
        over the text and nothing else.

        None means "no cheap gate for this intent", so every line is parsed.
        Correct but slow, which is the right default for an intent an
        adapter has not thought about.
        """
        return None

    def begin_file(self, path: Path | None = None) -> None:  # noqa: B027 — opt-in hook
        """Called once before the first line of a transcript.

        The hook exists so an adapter whose harness splits one logical event
        across lines can hold correlation state for the duration of a file.
        Stateless adapters ignore it. Events are still emitted by
        :meth:`normalize`, one line at a time; this scopes state, it is not
        a flush protocol.

        *path* is the transcript being opened when the drive path knows it,
        and None when the caller supplied bare lines.
        """

    def end_file(self) -> None:  # noqa: B027 — opt-in hook
        """Called after the last line of a transcript, including on error."""

    def events_from_lines(
        self,
        lines: Iterable[str],
        *,
        prefilter: RawPrefilter | None = None,
        path: Path | None = None,
    ) -> Iterator[Event]:
        """Normalized events from already-decoded transcript *lines*.

        The one read loop: gate, normalize, skip unparseable, bracketed by
        the per-file lifecycle. Every drive path routes through here; only
        the source of lines differs (a whole file, a cursored tail, or a
        handle the caller already holds).

        The gate runs on the raw string and decides *before* :meth:`normalize`
        is paid for. That ordering is the layer's performance contract, not an
        implementation detail; a line that fails the gate is never parsed.

        One adapter drives one transcript at a time. This is a generator, so
        the lifecycle brackets iteration rather than the call: ``begin_file``
        runs on the first ``next()`` and ``end_file`` when the iterator is
        exhausted, closed, or collected. A caller that opens a second iterator
        on the same adapter before draining the first would interleave two
        files into one adapter's state, so callers iterate one at a time.
        """
        self.begin_file(path)
        try:
            for raw in lines:
                if prefilter is not None and not prefilter(raw):
                    continue
                event = self.normalize(raw)
                if event is not None:
                    yield event
        finally:
            self.end_file()

    def iter_events(self, path: Path, *, prefilter: RawPrefilter | None = None) -> Iterator[Event]:
        """Events from one transcript file, in order.

        Lines failing *prefilter* are skipped without being parsed. Decoding
        is tolerant (``errors="replace"``); ``OSError`` propagates.
        """
        with path.open(encoding="utf-8", errors="replace") as fh:
            yield from self.events_from_lines(fh, prefilter=prefilter, path=path)
