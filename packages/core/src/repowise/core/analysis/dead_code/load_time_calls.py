"""Unused exports whose declaration runs a call when the module loads.

``export const actionZoom = register({...})`` is never named again, yet
deleting the line removes what ``register`` did at import time (an entry in a
registry that is looked up by string). No reference search can see that, so
such a finding keeps its confidence but is never deletion-ready: the deletion
gate reads :attr:`DeadCodeFindingData.runs_at_load`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .dynamic_markers import read_source_text
from .models import DeadCodeFindingData, DeadCodeKind

#: Kinds whose declaration can carry an initializer.
_VALUE_KINDS = frozenset({"constant", "variable"})

#: The assignment of a declaration: one ``=`` that is not part of ``==``,
#: ``=>``, ``<=``, ``>=`` or ``!=``.
_ASSIGN_RE = re.compile(r"(?<![=!<>:])=(?![=>])")

#: An initializer that starts by calling something: ``f(``, ``a.b(``,
#: ``Lazy::new(``, ``a?.b(``, ``new X(``, ``await f(``, ``f<T>(``. Function and
#: arrow literals do not run their body, so ``function``, ``async`` and
#: ``func`` heads are not calls.
_CALL_RE = re.compile(
    r"(?:await\s+)?(?:new\s+)?"
    r"(?!(?:function|async|func|fun|lambda)\b)"
    r"[A-Za-z_$][\w$]*(?:\s*(?:\.|::|\?\.)\s*[A-Za-z_$][\w$]*)*\s*(?:<[^()=;]*>)?\s*\("
)

#: What follows a parenthesized head when the head is called: ``(...)(`` (an
#: IIFE) or ``(...).name(``.
_CALLED_AFTER_PAREN_RE = re.compile(r"\s*(?:\(|\.\s*[A-Za-z_$][\w$]*\s*\()")

#: How much of the declaration to read: the initializer starts on its first lines.
_HEAD_CHARS = 600


def mark_load_time_calls(
    findings: list[DeadCodeFindingData],
    source_map: dict[str, bytes],
    repo_root: Path | str | None = None,
) -> list[DeadCodeFindingData]:
    """Set ``runs_at_load`` on unused value exports initialized by a call.

    A file missing from *source_map* is read from *repo_root*; one that cannot
    be read at all is marked, since nothing proves its initializer is inert.
    Mutates in place and returns the same list.
    """
    for finding in findings:
        if _candidate(finding) and _initialized_by_call(finding, source_map, repo_root):
            finding.runs_at_load = True
    return findings


def _candidate(finding: DeadCodeFindingData) -> bool:
    return (
        finding.kind is DeadCodeKind.UNUSED_EXPORT
        and finding.symbol_kind in _VALUE_KINDS
        and finding.is_spanned_symbol
    )


def _initialized_by_call(
    finding: DeadCodeFindingData,
    source_map: dict[str, bytes],
    repo_root: Path | str | None,
) -> bool:
    if finding.start_line is None or finding.end_line is None:
        return False
    abs_path = str(Path(repo_root) / finding.file_path) if repo_root is not None else ""
    try:
        source = read_source_text(finding.file_path, abs_path, source_map)
    except OSError:
        return True
    lines = source.splitlines()
    head = "\n".join(lines[finding.start_line - 1 : finding.end_line])[:_HEAD_CHARS]
    name = re.search(rf"(?<![\w$]){re.escape(finding.symbol_name or '')}(?![\w$])", head)
    if name is None:
        return False
    assign = _top_level_assignment(head, name.end())
    if assign is None:
        return False
    value = head[assign:].lstrip()
    if value.startswith("("):
        return _parenthesized_head_is_called(value)
    return _CALL_RE.match(value) is not None


def _parenthesized_head_is_called(value: str) -> bool:
    """Whether ``(...)`` at the start of *value* is called (``(() => {...})()``).

    An arrow head (``(a) =>``) or a grouped expression (``(60 * 1000)``) is
    not. A group that does not close within the head read is a long body, most
    likely an IIFE, and wrongly withholding one costs a deletion-ready row
    where wrongly passing one costs a broken program.
    """
    depth = 0
    for i, char in enumerate(value):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return _CALLED_AFTER_PAREN_RE.match(value, i + 1) is not None
    return True


def _top_level_assignment(head: str, start: int) -> int | None:
    """The offset just past the declaration's ``=``, skipping any inside brackets.

    ``x: Map<K = string> = make()`` carries a default type argument; only the
    ``=`` at depth 0 starts the initializer. The ``>`` of a ``=>`` in a type
    annotation closes nothing.
    """
    for match in _ASSIGN_RE.finditer(head, start):
        between = head[start : match.start()]
        opened = sum(between.count(c) for c in "<([{")
        closed = sum(between.count(c) for c in ")]}") + between.count(">") - between.count("=>")
        if opened == closed:
            return match.end()
    return None


__all__ = ["mark_load_time_calls"]
