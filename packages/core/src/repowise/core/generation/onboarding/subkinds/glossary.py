"""Onboarding subkind: Glossary.

The words this repository uses for itself, defined in its own sentences. Every
row is read from the repository's own documents: the term, the sentence nearest
it, and the path that sentence came from. Nothing here is written for the page.

That is the whole design. A glossary is the page where an invented definition
does the most damage — it is the one a reader consults *because* they do not
know the answer, so they have nothing to check it against. So this page has no
model in its path at all: the spec is registered ``deterministic``, and two
renders of an unchanged repository are byte-identical.

**Unless the team declared a glossary.** A root ``CONTEXT.md`` (or a
``GLOSSARY.md``, or the per-context files a ``CONTEXT-MAP.md`` names) is a
person writing down which word is canonical and which synonyms are wrong. That
is the prescriptive half of the same artifact, and it is the authority: a term
from it leads the page, keeps its row even with no corroboration from the code
(the gap is the signal), carries its ``_Avoid_`` list into a column, and
outranks the mined term that spells the same phrase. Mined terms the glossary
does not mention keep the contract they always had — quote-never-invent, cited,
and admitted only with structural corroboration. A mined term whose phrase the
glossary marks ``_Avoid_`` is demoted to the tail rather than deleted: the row
is evidence that the code and the team disagree, and that evidence is the whole
point of holding the file.

The cost is honesty about coverage. A term the repository names without ever
defining renders with an em dash rather than a plausible sentence, and a
repository whose documents define nothing gets no page. Both are the correct
answers to a question this page cannot answer from the code.

Gate: at least five terms survive selection. Below that the page is a handful
of rows pretending to be a vocabulary. A declared glossary bypasses the floor —
the team has already opted in by writing the file, and refusing to render four
terms they deliberately wrote would be the tool overruling them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from ...declared_glossary import DeclaredTerm
from ...house_vocabulary import SelectedTerm, select_terms
from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_GLOSSARY, SLOT_TITLES

log = structlog.get_logger(__name__)

#: Below this the page is not a vocabulary. Five is the floor the track set:
#: enough rows that a reader consults it rather than reads it. Bypassed when a
#: declared glossary exists.
_GATE_MIN_TERMS = 5
#: A lookup surface can be long — a reader scans for one row and ignores the
#: rest — but not unbounded. Forty is roughly two screens and is far above what
#: any repository measured so far corroborates.
_MAX_TERMS = 40
#: How many synonyms to name per row before the cell stops being scannable.
_MAX_AVOID = 4
#: How many subsystems to name per row before the cell stops being scannable.
_MAX_USED_IN = 3


@dataclass(frozen=True)
class GlossaryEntry:
    """One row of the glossary: mined, or declared by the team."""

    term: str
    #: The repository's own sentence about the term, or ``None`` when it named
    #: the term without defining it. On a declared row this is the team's own
    #: sentence, quoted the same way.
    definition: str | None
    #: The document the sentence was read from. Always a real path.
    source_path: str
    #: The parts of the system that name it, from the structural side. Empty on
    #: a declared term the code has not caught up to yet.
    used_in: tuple[str, ...]
    #: Whether the codebase defines a symbol by this name, so the template
    #: knows whether backticks would survive a grounding pass.
    is_indexed_symbol: bool
    #: ``"declared"`` or ``"mined"``, rendered so a reader can tell which rows
    #: are the team's ruling and which are the repository's own usage.
    status: str = "mined"
    #: The synonyms the team marked wrong for this concept, as written.
    avoid: tuple[str, ...] = ()
    #: The bounded context the term belongs to, or ``None``.
    context: str | None = None
    #: The canonical term to use instead, on a demoted mined row.
    demoted_by: str | None = None
    #: Nearest authoritative prose for bounded provider synthesis. It may be a
    #: non-definition, which is why it is separate from ``definition``.
    definition_evidence: str | None = None
    definition_evidence_source: str | None = None


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
    #: How many rows came from a glossary the team authored. Zero on every
    #: repository that has not declared one, which is most of them.
    declared: int = 0
    #: How many mined rows the declared glossary demoted.
    demoted: int = 0
    #: How many declared rows the code does not spell yet — the gap DDD treats
    #: as the signal rather than as noise.
    not_yet_in_code: int = 0


def _entry(selected: SelectedTerm) -> GlossaryEntry:
    names = selected.corroborating_names
    used_in = names[:_MAX_USED_IN]
    if len(names) > _MAX_USED_IN:
        # Said, not silently dropped. A truncated list reads as the whole list,
        # and "where is this used" is the column a reader acts on.
        used_in = (*used_in, f"and {len(names) - _MAX_USED_IN} more")
    avoid = selected.avoid[:_MAX_AVOID]
    if len(selected.avoid) > _MAX_AVOID:
        avoid = (*avoid, f"and {len(selected.avoid) - _MAX_AVOID} more")
    return GlossaryEntry(
        term=selected.term,
        definition=selected.definition,
        source_path=selected.source_path,
        used_in=used_in,
        is_indexed_symbol=selected.is_indexed_symbol,
        status=selected.status,
        avoid=avoid,
        context=selected.context,
        demoted_by=selected.demoted_by,
        definition_evidence=selected.definition_evidence,
        definition_evidence_source=selected.definition_evidence_source,
    )


def _build_from(
    *,
    repo_name: str,
    house_terms: tuple,
    declared: tuple[DeclaredTerm, ...],
    module_corroboration: tuple[str, ...],
) -> GlossaryContext | None:
    """The page's context, from the same selection every other caller reads."""
    if not house_terms and not declared:
        # Already logged where the mining happens, with the three counts that
        # separate "no repository to read" from "nothing written" from
        # "nothing built". Repeating them here would say less.
        return None

    # Every selected term, and no further test. A row whose definition column
    # is an em dash still carries facts a reader came for — the repository has
    # a word for this, and here are the parts of the system that use it — and
    # dropping those rows was measured to cost more than it saved: requiring a
    # single word to arrive with a definition removed "Workspace", "Coupling",
    # "CLI", "Distill", "Costs" and "Decisions" here and "Security" on django,
    # to remove two weak rows. A lookup page is judged on coverage.
    #
    # The declared half adds its own carve-out: a term a person wrote down
    # keeps its row with no corroboration at all, because bind-or-drop is the
    # right rule for vocabulary a document invented and the wrong one for a
    # word the team has agreed to use before the code catches up.
    #
    # Selected whole, then capped. The count before the cap is carried onto the
    # page: the footer explains that a mined term reaches it only with
    # structural corroboration, and that is a false account of any term dropped
    # for length instead.
    selected_all = select_terms(
        house_terms,
        module_corroboration,
        declared=declared,
    )
    selected = selected_all[:_MAX_TERMS]
    corroborated = sum(1 for term in selected_all if term.corroborating_pages)
    declared_rows = [term for term in selected if term.status == "declared"]
    if not declared_rows and len(selected) < _GATE_MIN_TERMS:
        log.info(
            "onboarding.glossary_gate_skipped",
            repo_name=repo_name,
            mined=len(house_terms),
            corroborated=corroborated,
            required=_GATE_MIN_TERMS,
        )
        return None

    # Alphabetical, because a glossary is looked up rather than read. Ranking
    # decided which terms are here — and put the declared ones first, which is
    # the part that matters before this sort — and has no job left once they
    # are.
    entries = sorted((_entry(term) for term in selected), key=lambda e: e.term.lower())

    sources: list[str] = []
    for entry in entries:
        if entry.source_path not in sources:
            sources.append(entry.source_path)
    defined = sum(1 for entry in entries if entry.definition)

    log.info(
        "onboarding.glossary_built",
        repo_name=repo_name,
        mined=len(house_terms),
        declared=len(declared_rows),
        demoted=sum(1 for entry in entries if entry.demoted_by),
        corroborated=corroborated,
        terms=len(entries),
        defined=defined,
        documents=len(sources),
    )
    return GlossaryContext(
        repo_name=repo_name,
        entries=entries,
        sources=sources,
        mined=len(house_terms),
        corroborated=corroborated,
        defined=defined,
        declared=len(declared_rows),
        demoted=sum(1 for entry in entries if entry.demoted_by),
        not_yet_in_code=sum(1 for entry in declared_rows if not entry.corroborating_pages),
    )


def _build(signals: OnboardingSignals) -> GlossaryContext | None:
    return _build_from(
        repo_name=signals.repo_name,
        house_terms=tuple(signals.house_terms),
        declared=tuple(signals.declared_terms),
        module_corroboration=signals.module_corroboration,
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
