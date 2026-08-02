"""Deterministic tables embedded into the repository overview.

The overview is written by a model, and enumerable facts written by a model are
resampled on every render: two calls with the same prompt, the same model and
the same temperature produced pages that disagreed on their row count and on
which paths they cited. Facts the run already holds — which packages exist,
where they are, how big they are — are built here instead and embedded after
the page comes back, the same way the architecture map already is.

That makes them identical on the model-written page and on the structure-only
page, stable across updates that changed no code, and assertable in a test.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import structlog

from .concept_tree.vocabulary import HouseTerm, phrase_pattern, term_words

log = structlog.get_logger(__name__)

PACKAGE_TABLE_HEADING = "## Packages"
CAPABILITY_TABLE_HEADING = "## What it does"

# The heading plus everything up to the next heading of the same or higher
# level. Anchored at a line start so a mention inside prose is not a match.
_PACKAGE_SECTION_RE = re.compile(
    r"^##[ \t]+Packages[ \t]*\n(?:.*?)(?=^#{1,2}[ \t]|\Z)",
    re.MULTILINE | re.DOTALL,
)


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

    section = f"{PACKAGE_TABLE_HEADING}\n\n{table}\n\n"
    if _PACKAGE_SECTION_RE.search(content):
        # Function replacement: table cells carry backslashes and backticks
        # that re.sub would otherwise read as group references.
        return _PACKAGE_SECTION_RE.sub(lambda _m: section, content, count=1)
    sep = "" if content.endswith("\n") else "\n"
    return f"{content}{sep}\n{section}"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------
#
# The overview's opening question is "what does this thing do", and the answer
# should be in the repository's own words rather than in the generic vocabulary
# of static analysis. The mined house terms are those words. Ranked by document
# frequency alone they are not front-page material: on this repository the top
# of that list holds the repository's own name, "DONE", "Architecture" and
# "Files changed", which would put junk in half the rows.
#
# So a term reaches this table only with corroboration from a second,
# independently-derived artifact: a module page — built from the dependency
# graph, with no knowledge of the documents — has to name it in its title or
# its summary. That needs no stopword list and no per-repository tuning.

_MAX_CAPABILITY_ROWS = 6
#: Long enough for a sentence, short enough that a row stays one line to read.
_MAX_CAPABILITY_DEFINITION = 160

_CAPABILITY_SECTION_RE = re.compile(
    r"^##[ \t]+What it does[ \t]*\n(?:.*?)(?=^#{1,2}[ \t]|\Z)",
    re.MULTILINE | re.DOTALL,
)


@dataclass(frozen=True)
class Capability:
    """One row: a mined term, what the repository says it is, and where from."""

    term: str
    #: The repository's own sentence, or ``None``. Never invented — a term the
    #: repository named without defining is still a capability, and writing a
    #: definition for it is the one thing the vocabulary miner refuses to do.
    definition: str | None
    #: The document or source file the sentence was read from, falling back to
    #: the first document that named the term. Always a real path.
    source_path: str
    #: How many module pages name it. Kept for the log, not rendered — it is
    #: the strength of the corroboration, not a fact about the repository.
    corroborating_pages: int


def select_capabilities(
    house_terms: Sequence[HouseTerm],
    module_names: Iterable[str],
    *,
    limit: int = _MAX_CAPABILITY_ROWS,
) -> list[Capability]:
    """The terms worth putting on the front page, in the order they go there.

    ``module_names`` are what the structural side calls the parts of the
    system: the module groups' titles. They are the corroborating artifact — a
    module group is cut from the dependency graph and named from the code, so
    a term appearing in one was arrived at twice, from the documents and from
    the structure, independently.

    Titles only. Community labels and directory keys were tried and measured
    worse: a label like "Ingestion and Analysis Engine" is broad enough to
    corroborate almost any single common word, which put "Architecture" and
    "Workspace" on the front page of a real render.

    Groups rather than written module *pages*, deliberately. A group exists on
    every run, so a scoped run that regenerates the overview alone selects the
    same rows as a full one — a front-page section that shrinks depending on
    how generation was invoked is the instability these deterministic tables
    exist to remove. It is also the stronger claim of independence: a group's
    name is computed, while a page's summary is model-written and resampled.

    The cost is reach. Measured on django, matching titles and summaries of
    written pages corroborates 5 terms where names alone corroborate 2. The
    upgrade path is to read module summaries out of the store rather than out
    of this run, which would be both stable and rich; it needs level 6 to
    become async and the vector store to be present, so it is not done here.

    **Multi-word terms come first.** Not as a filter — a single word still
    reaches the table when there is room — but ahead of single words, because
    a subsystem is nearly always named with two words ("blast radius", "dead
    code", "change risk") and an ordinary English word with one. The ranking
    in :func:`~...vocabulary.extract_house_terms` already encodes that as its
    tiebreak; here it leads, because document frequency barely discriminates
    at six rows (most repositories have two or three documents worth mining,
    so nearly every term ties at one) and one junk row out of six is expensive.

    Ordering is total and derived only from the inputs, so two runs over an
    unchanged repository select the same rows in the same order.
    """
    corpus = [name for name in module_names if name]
    selected: list[Capability] = []
    for term in house_terms:
        pattern = phrase_pattern(term.term)
        hits = sum(1 for page in corpus if pattern.search(page))
        if not hits:
            continue
        source = term.definition_source or (term.source_paths[0] if term.source_paths else None)
        if source is None:
            # A term with no path at all cannot be cited, and an uncitable row
            # on the front page is the shape of claim this wiki does not make.
            continue
        selected.append(
            Capability(
                term=term.term,
                definition=term.definition,
                source_path=source,
                corroborating_pages=hits,
            )
        )

    selected.sort(key=lambda c: (len(term_words(c.term)) == 1, -c.corroborating_pages, c.term))
    kept = selected[:limit]
    log.info(
        "overview_capabilities_selected",
        mined=len(house_terms),
        corroborated=len(selected),
        kept=len(kept),
        terms=[c.term for c in kept],
    )
    if house_terms and not selected:
        # The documents name things the structure does not. That is a real
        # answer about a repository — marketing vocabulary with no cluster
        # behind it — but it is also what a corroboration corpus arriving
        # empty looks like, so the two counts that separate them are logged
        # rather than the section just not appearing.
        log.warning(
            "overview_capabilities_uncorroborated",
            mined=len(house_terms),
            module_names=len(corpus),
        )
    return kept


def _cell(text: str) -> str:
    """Text safe to put in a markdown table cell.

    A pipe ends the cell wherever it appears, and mined prose is the
    repository's text rather than ours — a definition that quotes a shell
    pipeline or a reStructuredText grid row would otherwise shift every column
    to its right.
    """
    return " ".join(text.split()).replace("|", "\\|")


def build_capability_table(capabilities: Sequence[Capability]) -> str | None:
    """Render ``Capability | What it is | Where it is written`` as markdown.

    Returns ``None`` when nothing was selected. A repository whose documents
    name nothing its code also spells is a supported and common outcome, and a
    header over an empty table says less than no section at all.
    """
    if not capabilities:
        log.info("overview_capability_table_empty")
        return None

    lines = [
        "| Capability | What it is | Where it is written |",
        "|---|---|---|",
    ]
    for cap in capabilities:
        definition = _cell(cap.definition) if cap.definition else "—"
        if len(definition) > _MAX_CAPABILITY_DEFINITION:
            definition = definition[: _MAX_CAPABILITY_DEFINITION - 1].rstrip() + "…"
        lines.append(f"| {_cell(cap.term)} | {definition} | `{_cell(cap.source_path)}` |")
    log.debug("overview_capability_table_built", rows=len(capabilities))
    return "\n".join(lines)


def embed_capability_table(content: str, table: str | None) -> str:
    """Return *content* with *table* under ``## What it does``, idempotently.

    Same contract as :func:`embed_package_table`: an existing section of that
    name is replaced wholesale, so a reused or cached page picks up the current
    selection instead of accumulating a second one.
    """
    if not table:
        return content

    section = f"{CAPABILITY_TABLE_HEADING}\n\n{table}\n\n"
    if _CAPABILITY_SECTION_RE.search(content):
        return _CAPABILITY_SECTION_RE.sub(lambda _m: section, content, count=1)
    sep = "" if content.endswith("\n") else "\n"
    return f"{content}{sep}\n{section}"
