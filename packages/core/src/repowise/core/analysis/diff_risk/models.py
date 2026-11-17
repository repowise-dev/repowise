"""Typed vocabulary of the diff-risk rules."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .text import FileView, Hit

Rule = Literal["added-suppression", "weakened-tests", "test-only-branch"]
Severity = Literal["off", "warn", "error"]

RULES: tuple[Rule, ...] = ("added-suppression", "weakened-tests", "test-only-branch")

#: ``(path, language) -> is a test file``.
TestClassifier = Callable[[str, "str | None"], bool]


@dataclass(frozen=True, slots=True)
class FileTexts:
    """Both sides of one changed file. ``None`` means the side has no file."""

    base_path: str | None
    head_path: str | None
    base: str | None
    head: str | None

    @property
    def path(self) -> str:
        return self.head_path or self.base_path or ""


@dataclass(frozen=True, slots=True)
class DiffRiskFinding:
    """One diff-risk finding. Every one is introduced by the change."""

    #: Line-free, stable across runs and unrelated edits.
    finding_id: str
    rule: Rule
    #: ``comment`` | ``attribute`` | ``config`` | ``skip-marker`` |
    #: ``removed-tests`` | ``assertion-drop`` | ``threshold`` | ``test-env-check``.
    kind: str
    path: str
    #: 1-based head line; ``None`` for a file-level finding.
    line: int | None
    severity: Severity
    message: str
    #: The marker or the value change that supports the finding.
    evidence: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RawFinding:
    """A finding before its severity and id: what a rule reports."""

    rule: Rule
    kind: str
    path: str
    line: int | None
    #: What the finding is about, for its line-free id (a marker, a key).
    label: str
    message: str
    evidence: str


#: ``(rule, kind, hit)``: one marker occurrence on one side of a file.
Tagged = tuple[Rule, str, Hit]


@dataclass(frozen=True, slots=True)
class Lens:
    """What one changed file is, and so which line rules read it."""

    language: str | None
    prose: bool
    test: bool
    production: bool


@dataclass(slots=True)
class TestFile:
    """Both sides of one changed test file, for the counting rules."""

    __test__ = False  # not a pytest class

    path: str
    base: FileView | None
    head: FileView | None
