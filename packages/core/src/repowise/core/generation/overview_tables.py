"""Deterministic tables embedded into the repository overview.

The overview is written by a model, and enumerable facts written by a model are
resampled on every render: two calls with the same prompt, the same model and
the same temperature produced pages that disagreed on their row count and on
which paths they cited. Facts the run already holds — which packages exist,
where they are, how big they are — are built here instead and embedded after
the page comes back, the same way the system map is.

That makes them identical on the model-written page and on the structure-only
page, stable across updates that changed no code, and assertable in a test.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import PurePosixPath

import structlog

from ..support_paths import DOC_EXTENSIONS
from .concept_tree.vocabulary import HouseTerm
from .house_vocabulary import SelectedTerm, cell, clamp, select_terms

log = structlog.get_logger(__name__)

#: One row of the capability table. The selection and the definition test live
#: in :mod:`house_vocabulary`; this name is what the overview calls it.
Capability = SelectedTerm

PACKAGE_TABLE_HEADING = "## Packages"
CAPABILITY_TABLE_HEADING = "## What it does"

# The heading plus everything up to the next heading of the same or higher
# level. Anchored at a line start so a mention inside prose is not a match.
#
# The provenance footer carries no heading, only a horizontal rule, so it is a
# terminator too. Without it a section that is last on the page runs to the end
# and the replacement eats the footer — which is what happened the moment the
# deterministic overview stopped rendering a path table below its packages.
_SECTION_END = r"(?=^#{1,2}[ \t]|^---[ \t]*$|\Z)"


def embed_section(content: str, heading: str, body: str | None) -> str:
    """Return *content* with *body* under ``## heading``, idempotently.

    An existing section of that name is replaced wholesale, whether a previous
    run embedded it or the model wrote the heading itself, so a page reused
    from an earlier run carries one copy of the current facts. With no *body*
    the section is removed: the reused page still carries what that run
    embedded, and the current facts have nothing to say there.
    """
    pattern = re.compile(
        r"^##[ \t]+" + re.escape(heading) + r"[ \t]*\n(?:.*?)" + _SECTION_END,
        re.MULTILINE | re.DOTALL,
    )
    if not body:
        return pattern.sub("", content, count=1)
    section = f"## {heading}\n\n{body}\n\n"
    if pattern.search(content):
        # Function replacement: table cells carry backslashes and backticks
        # that re.sub would otherwise read as group references.
        return pattern.sub(lambda _m: section, content, count=1)
    sep = "" if content.endswith("\n") else "\n"
    return f"{content}{sep}\n{section}"


def build_package_table(package_stats: list[dict]) -> str | None:
    """Render ``Package | Path | Files | Languages`` as a markdown table.

    Returns ``None`` when the repository has no packages to tabulate — a
    single-package repository is the common case and a header with no rows
    under it is worse than no section.
    """
    if not package_stats:
        # Not an error: most repositories are not monorepos. Logged because a
        # table that silently stops appearing is a failure this page has
        # already shipped once.
        log.info("overview_package_table_empty")
        return None

    lines = [
        "| Package | Path | Files | Languages |",
        "|---|---|---|---|",
    ]
    for pkg in package_stats:
        langs = ", ".join(pkg.get("languages") or []) or "—"
        lines.append(f"| {pkg['name']} | `{pkg['path']}` | {pkg.get('files', 0)} | {langs} |")
    log.debug("overview_package_table_built", packages=len(package_stats))
    return "\n".join(lines)


def embed_package_table(content: str, table: str | None) -> str:
    """Return *content* with *table* under ``## Packages``, idempotently.

    Replaces an existing ``## Packages`` section wholesale, whether it is one
    this function wrote on a previous update or one the model wrote itself —
    the model writes that heading unprompted, and leaving both would give the
    reader the package list twice, once counted and once sampled.
    """
    if not table:
        return content
    return embed_section(content, PACKAGE_TABLE_HEADING.removeprefix("## "), table)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
#
# The overview's opening question is "what does this thing do", and the answer
# should be in the repository's own words rather than in the generic vocabulary
# of static analysis. The mined house terms are those words, selected and
# corroborated by :func:`~house_vocabulary.select_terms`; the front page takes
# the head of that list.

MAX_CAPABILITY_ROWS = 6
#: Long enough for a sentence, short enough that a row stays one line to read.
_MAX_CAPABILITY_DEFINITION = 160


def select_capabilities(
    house_terms: Sequence[HouseTerm],
    module_names: Iterable[str],
    *,
    limit: int = MAX_CAPABILITY_ROWS,
) -> list[Capability]:
    """The terms worth putting on the front page, in the order they go there.

    The head of :func:`~house_vocabulary.select_terms`. Only terms a document defines: a front-page row reading "—" says nothing,
    and a docstring describes one function rather than what the product does
    ("Decisions are co-located in the page vector store ..." was the whole
    table here once). Filtered before the cap so defined terms fill it.
    """
    defined = [
        t
        for t in select_terms(house_terms, module_names)
        if t.definition and PurePosixPath(t.source_path).suffix.lower() in DOC_EXTENSIONS
    ]
    return defined[:limit]


def build_capability_table(capabilities: Sequence[Capability]) -> str | None:
    """Render ``Capability | What it is | Where it is written`` as markdown.

    Returns ``None`` when no selected row carries a definition. A repository whose documents
    name nothing its code also spells is a supported and common outcome, and a
    header over an empty table says less than no section at all.
    """
    # An undefined row renders as "—" and says nothing; no rows means no table.
    rows = [cap for cap in capabilities if cap.definition]
    if not rows:
        log.info("overview_capability_table_empty", undefined=len(capabilities))
        return None

    lines = [
        "| Capability | What it is | Where it is written |",
        "|---|---|---|",
    ]
    for cap in rows:
        definition = clamp(cap.definition, _MAX_CAPABILITY_DEFINITION)
        lines.append(f"| {cell(cap.term)} | {definition} | `{cell(cap.source_path)}` |")
    log.debug("overview_capability_table_built", rows=len(rows))
    return "\n".join(lines)


def embed_capability_table(content: str, table: str | None) -> str:
    """Return *content* with *table* under ``## What it does``, idempotently.

    :func:`embed_section`: with no table the section is removed, so a page
    reused from an earlier run loses rows the current selection no longer has.
    """
    return embed_section(content, CAPABILITY_TABLE_HEADING.removeprefix("## "), table)
