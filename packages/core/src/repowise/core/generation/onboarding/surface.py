"""What a user types or calls, as the reader-facing pages list it.

The commands, tools and public names come from :mod:`..flow_entries`, the one
detector the walkthrough pages are selected from as well, so the front page,
Getting Started and the walkthroughs name the same operations. This module
adds what a reader needs on top: each entry's first docstring line and the
``path:line`` of its own declaration, ranked by how often the documentation
shows it in use. It also reads the opening and stages of a written walkthrough
page (:func:`journey`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..flow_entries import (
    API,
    CLI,
    TOOL,
    DocSection,
    Entry,
    LineReader,
    declared_entries,
    doc_mentions,
    entry_cite,
    program_name,
    python_scripts,
    text_commands,
)

_MAX_COMMANDS = 20
_MAX_TOOLS = 15
_MAX_API = 8


@dataclass(frozen=True)
class Surface:
    """Something a user or another program calls, and where it is declared."""

    name: str
    doc: str
    cite: str


@dataclass(frozen=True)
class Surfaces:
    """Everything a reader types or calls, documented ones first."""

    programs: list[str] = field(default_factory=list)
    commands: list[Surface] = field(default_factory=list)
    tools: list[Surface] = field(default_factory=list)
    api: list[Surface] = field(default_factory=list)


@dataclass(frozen=True)
class Journey:
    """A walkthrough page already written for one operation."""

    title: str
    opening: str
    stages: str


def _first_doc_line(graph: Any, node: str) -> str:
    doc = (graph.nodes.get(node, {}) if node in graph else {}).get("docstring") or ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def _ranked(
    entries: Sequence[Entry], graph: Any, sections: Sequence[DocSection], limit: int
) -> list[Surface]:
    """Unique by public name, the ones the documentation shows in use most first."""
    unique: dict[str, Entry] = {}
    for entry in entries:
        unique.setdefault(entry.public, entry)
    counts = {e.public: doc_mentions(e, sections) for e in unique.values()}
    ranked = sorted(unique.values(), key=lambda e: (-counts[e.public], e.node))
    return [Surface(e.public, _first_doc_line(graph, e.node), entry_cite(graph, e)) for e in ranked[:limit]]


def surfaces(
    graph: Any, read: LineReader, repo_root: Path | str | None, sections: Sequence[DocSection]
) -> Surfaces:
    """The programs, commands, tools and public names of the repository."""
    program = program_name(repo_root, graph)
    entries = declared_entries(graph, read, program, python_scripts(repo_root))
    files = [n for n, a in graph.nodes(data=True) if a.get("node_type") == "file"]
    commands = [e for e in entries if e.kind == CLI] + text_commands(files, read, program)
    api = [e for e in entries if e.kind == API and doc_mentions(e, sections)]
    return Surfaces(
        programs=[program] if program else [],
        commands=_ranked(commands, graph, sections, _MAX_COMMANDS),
        tools=_ranked([e for e in entries if e.kind == TOOL], graph, sections, _MAX_TOOLS),
        api=_ranked(api, graph, sections, _MAX_API),
    )


# -- journeys ----------------------------------------------------------------


def _paragraphs(body: str) -> list[str]:
    """Prose paragraphs of a markdown body, outside fences, headings and tables."""
    out: list[str] = []
    fenced = False
    for block in re.split(r"\n\s*\n", body):
        stripped = block.strip()
        if stripped.count("```") % 2:
            fenced = not fenced
            continue
        if fenced or not stripped or stripped.startswith(("#", "|", "```", "**", "<")):
            continue
        out.append(" ".join(stripped.split()))
    return out


def _section(content: str, heading: str) -> str:
    m = re.search(rf"(?m)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", content, re.S)
    return m.group(1).strip() if m else ""


def journey(title: str, content: str, *, max_chars: int = 1400) -> Journey:
    """The opening and the stage list of a written walkthrough page.

    Stages are the page's "At a glance" list when it has one, else the names
    of its sections, which name the phases of the operation.
    """
    m = re.search(r"(?m)^# (.+)$", content)
    title = m.group(1).strip() if m else title
    body = content[m.end() :] if m else content
    head = body.split("\n## ", 1)[0]
    opening = " ".join(_paragraphs(head)[:2])[: max_chars // 2]
    stages = _section(content, "At a glance")
    if not stages:
        names = re.findall(r"(?m)^## (.+)$", content)
        stages = "\n".join(f"- {n.strip()}" for n in names if not n.lower().startswith("where to read"))
    return Journey(title=title, opening=opening, stages=stages[: max_chars // 2])
