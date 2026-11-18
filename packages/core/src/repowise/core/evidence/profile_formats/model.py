"""What a profile reader hands the converter: frames and weighted stacks, nothing joined."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from repowise.core.ingestion.languages.registry import REGISTRY

if TYPE_CHECKING:
    from repowise.core.frame_resolver import FrameLanguage, ProfileFrame

#: How much of the file a sniffer sees.
SNIFF_BYTES = 4096


@dataclass(frozen=True)
class Stack:
    """One distinct call stack and what the run spent on it."""

    #: Indices into :attr:`ProfileRun.frames`, root first, leaf last.
    frames: tuple[int, ...]
    #: Time or samples on this stack, in the run's unit.
    weight: float
    #: Samples taken on this stack; None when the format records durations only.
    samples: int | None


@dataclass(frozen=True)
class ProfileRun:
    """A profile as its tool wrote it, before any frame is joined to the code."""

    frames: tuple[ProfileFrame, ...]
    stacks: tuple[Stack, ...]
    #: ``samples`` when weights count samples, else a time unit (``s``, ``ms``, ``us``, ``ns``).
    unit: str
    #: ``cpu`` or ``wall`` when the tool says; None leaves it to the caller.
    value_kind: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None
    #: Records the reader could not use, by reason (a malformed line).
    skipped: dict[str, int] = field(default_factory=dict)
    #: Tests that failed in a profiled test suite, when the profile says.
    tests_failed: int | None = None


class StackTally:
    """Weights per distinct stack, merged as a reader walks its samples."""

    def __init__(self) -> None:
        self._weights: dict[tuple[int, ...], float] = {}
        self._samples: Counter[tuple[int, ...]] = Counter()
        #: Stacks some weight reached without a sample count (an evented profile).
        self._uncounted: set[tuple[int, ...]] = set()

    def add(self, frames: tuple[int, ...], weight: float, samples: int | None = 1) -> None:
        self._weights[frames] = self._weights.get(frames, 0.0) + weight
        if samples is None:
            self._uncounted.add(frames)
        else:
            self._samples[frames] += samples

    def stacks(self) -> tuple[Stack, ...]:
        """The merged stacks; a stack's sample count is None once any of its weight had none."""
        return tuple(
            Stack(frames, weight, None if frames in self._uncounted else self._samples[frames])
            for frames, weight in self._weights.items()
        )


def frame_language(path: str) -> FrameLanguage:
    """The language a frame's file is written in, from its extension.

    Answered by the language registry; ``unknown`` for a file with no
    extension (a runtime frame such as ``<built-in>``). Any language but the
    three :func:`~repowise.core.frame_resolver.resolve_frames` checks is
    skipped there as ``unsupported_language``.
    """
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    tag = REGISTRY.from_extension(name[dot:].lower()) if dot > 0 else "unknown"
    return cast("FrameLanguage", tag)


__all__ = ["SNIFF_BYTES", "ProfileRun", "Stack", "StackTally", "frame_language"]
