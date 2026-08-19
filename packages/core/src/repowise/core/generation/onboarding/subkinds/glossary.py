"""Onboarding subkind: Glossary.

The words this repository uses for itself, defined in its own sentences. Every
row is read from the repository's own documents: the term, the sentence nearest
it, and the path that sentence came from. Nothing here is written for the page.

That is the whole design. A glossary is the page where an invented definition
does the most damage — it is the one a reader consults *because* they do not
know the answer, so they have nothing to check it against. So this page has no
model in its path at all: the spec is registered ``deterministic``, and two
renders of an unchanged repository are byte-identical.

The cost is honesty about coverage. A term the repository names without ever
defining renders with an em dash rather than a plausible sentence, and a
repository whose documents define nothing gets no page. Both are the correct
answers to a question this page cannot answer from the code.

Gate: at least five terms survive corroboration. Below that the page is a
handful of rows pretending to be a vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from ...house_vocabulary import SelectedTerm, select_terms
from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_GLOSSARY, SLOT_TITLES

log = structlog.get_logger(__name__)

#: Below this the page is not a vocabulary. Five is the floor the track set:
#: enough rows that a reader consults it rather than reads it.
_GATE_MIN_TERMS = 5
#: A lookup surface can be long — a reader scans for one row and ignores the
#: rest — but not unbounded. Forty is roughly two screens and is far above what
#: any repository measured so far corroborates.
_MAX_TERMS = 40
#: How many subsystems to name per row before the cell stops being scannable.
_MAX_USED_IN = 3


@dataclass(frozen=True)
class GlossaryEntry:
    """One row of the glossary, entirely mined."""

    term: str
    #: The repository's own sentence about the term, or ``None`` when it named
    #: the term without defining it.
    definition: str | None
    #: The document the sentence was read from. Always a real path.
    source_path: str
    #: The parts of the system that name it, from the structural side.
    used_in: tuple[str, ...]
    #: Whether the codebase defines a symbol by this name, so the template
    #: knows whether backticks would survive a grounding pass.
    is_indexed_symbol: bool


@dataclass
class GlossaryContext:
    repo_name: str
    entries: list[GlossaryEntry] = field(default_factory=list)
    #: Every document any surviving term was read from, deduplicated and
    #: ordered. Rendered as the page's provenance line.
    sources: list[str] = field(default_factory=list)
    #: How many terms were mined, how many survived corroboration, and how many
    #: of the rows carry a definition. All three are rendered: a glossary that
    #: quietly covers a third of its vocabulary, or that silently drops the tail
    #: of a long one, should say so on the page rather than imply completeness.
    mined: int = 0
    corroborated: int = 0
    defined: int = 0


def _entry(selected: SelectedTerm) -> GlossaryEntry:
    names = selected.corroborating_names
    used_in = names[:_MAX_USED_IN]
    if len(names) > _MAX_USED_IN:
        # Said, not silently dropped. A truncated list reads as the whole list,
        # and "where is this used" is the column a reader acts on.
        used_in = (*used_in, f"and {len(names) - _MAX_USED_IN} more")
    return GlossaryEntry(
        term=selected.term,
        definition=selected.definition,
        source_path=selected.source_path,
        used_in=used_in,
        is_indexed_symbol=selected.is_indexed_symbol,
    )


def _build(signals: OnboardingSignals) -> GlossaryContext | None:
    if not signals.house_terms:
        # Already logged where the mining happens, with the three counts that
        # separate "no repository to read" from "nothing written" from
        # "nothing built". Repeating them here would say less.
        return None

    # Every corroborated term, and no further test. A row whose definition
    # column is an em dash still carries two facts a reader came for — the
    # repository has a word for this, and here are the parts of the system
    # that use it — and dropping those rows was measured to cost more than it
    # saved: requiring a single word to arrive with a definition removed
    # "Workspace", "Coupling", "CLI", "Distill", "Costs" and "Decisions" here
    # and "Security" on django, to remove two weak rows. A lookup page is
    # judged on coverage.
    #
    # Selected whole, then capped. The count before the cap is carried onto the
    # page: the footer explains that a mined term reaches it only with
    # structural corroboration, and that is a false account of any term dropped
    # for length instead.
    corroborated = select_terms(signals.house_terms, signals.module_corroboration)
    selected = corroborated[:_MAX_TERMS]
    if len(selected) < _GATE_MIN_TERMS:
        log.info(
            "onboarding.glossary_gate_skipped",
            repo_name=signals.repo_name,
            mined=len(signals.house_terms),
            corroborated=len(corroborated),
            required=_GATE_MIN_TERMS,
        )
        return None

    # Alphabetical, because a glossary is looked up rather than read. Ranking
    # decided which terms are here; it has no job left once they are.
    entries = sorted((_entry(term) for term in selected), key=lambda e: e.term.lower())

    sources: list[str] = []
    for entry in entries:
        if entry.source_path not in sources:
            sources.append(entry.source_path)
    defined = sum(1 for entry in entries if entry.definition)

    log.info(
        "onboarding.glossary_built",
        repo_name=signals.repo_name,
        mined=len(signals.house_terms),
        corroborated=len(corroborated),
        terms=len(entries),
        defined=defined,
        documents=len(sources),
    )
    return GlossaryContext(
        repo_name=signals.repo_name,
        entries=entries,
        sources=sources,
        mined=len(signals.house_terms),
        corroborated=len(corroborated),
        defined=defined,
    )


register(
    SubkindSpec(
        slot=SLOT_GLOSSARY,
        title=SLOT_TITLES[SLOT_GLOSSARY],
        template="glossary.j2",
        build_context=_build,
        deterministic=True,
        needs_module_corroboration=True,
    )
)
