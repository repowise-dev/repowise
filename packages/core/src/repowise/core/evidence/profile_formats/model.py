"""What a profile reader hands the converter: frames and weighted stacks, nothing joined."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from repowise.core.ingestion.languages.registry import REGISTRY

if TYPE_CHECKING:
    from repowise.core.frame_resolver import ProfileFrame


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


class StackTally:
    """Weights per distinct stack, merged as a reader walks its samples."""

    def __init__(self) -> None:
        self._weights: dict[tuple[int, ...], float] = {}
        self._samples: Counter[tuple[int, ...]] = Counter()

    def add(self, frames: tuple[int, ...], weight: float, samples: int | None = 1) -> None:
        self._weights[frames] = self._weights.get(frames, 0.0) + weight
        if samples is not None:
            self._samples[frames] += samples

    def stacks(self, *, counted: bool) -> tuple[Stack, ...]:
        """The merged stacks; *counted* says whether sample counts were recorded."""
        return tuple(
            Stack(frames, weight, self._samples[frames] if counted else None)
            for frames, weight in self._weights.items()
        )


def frame_language(path: str) -> str:
    """The language a frame's file is written in, from its extension.

    Answered by the language registry; ``unknown`` for a file with no
    extension (a runtime frame such as ``<built-in>``).
    """
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return REGISTRY.from_extension(name[dot:].lower()) if dot > 0 else "unknown"


__all__ = ["ProfileRun", "Stack", "StackTally", "frame_language"]
