"""Onboarding subkind: Key Concepts.

The handful of words this project has for its own ideas, the ones a newcomer
meets on every other page: what each means, why it matters, and where in the
code it lives.

Concepts are the repository's own terms (``HouseTerm``), corroborated by the
structure: a term reaches the page only when a module group also names it, so
the word was arrived at twice, from the documents and from the code. Picking
the most-called symbols instead filled the page with argument records, file
writers and error types: central to the graph, meaningless to a reader.

Only terms a document defines: a docstring describes one function, not an idea
of the product. Gate: at least four such terms. Below that the page is skipped
rather than padded.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from ...overview_tables import select_capabilities
from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_KEY_CONCEPTS, SLOT_TITLES

log = structlog.get_logger(__name__)

_GATE_MIN_CONCEPTS = 4
_TOP_CONCEPTS = 6
_MAX_USED_IN = 3
_WORDS = (450, 700)


@dataclass(frozen=True)
class Concept:
    term: str
    #: The sentence a document of the repository wrote about the term.
    definition: str
    source_path: str
    #: The parts of the system that name it, from the structural side.
    used_in: tuple[str, ...]
    #: Whether the codebase defines a symbol by this name (backticks survive).
    is_indexed_symbol: bool


@dataclass
class KeyConceptsContext:
    repo_name: str
    concepts: list[Concept] = field(default_factory=list)
    layer_order: list[str] = field(default_factory=list)
    word_budget: tuple[int, int] = _WORDS


def _build(signals: OnboardingSignals) -> KeyConceptsContext | None:
    # The same selection the front page's terms come from: defined in a
    # document, not in a docstring, which describes one function.
    selected = select_capabilities(
        signals.house_terms, signals.module_corroboration, limit=_TOP_CONCEPTS
    )
    if len(selected) < _GATE_MIN_CONCEPTS:
        log.info(
            "onboarding.key_concepts_gate_skipped",
            repo_name=signals.repo_name,
            mined=len(signals.house_terms),
            defined_in_documents=len(selected),
            required=_GATE_MIN_CONCEPTS,
        )
        return None
    concepts = [
        Concept(
            term=t.term,
            definition=t.definition or "",
            source_path=t.source_path,
            used_in=tuple(t.corroborating_names[:_MAX_USED_IN]),
            is_indexed_symbol=t.is_indexed_symbol,
        )
        for t in selected
    ]
    log.info(
        "onboarding.key_concepts_built",
        repo_name=signals.repo_name,
        chosen=[c.term for c in concepts],
    )
    return KeyConceptsContext(
        repo_name=signals.repo_name,
        concepts=concepts,
        layer_order=list(signals.layer_order),
    )


register(
    SubkindSpec(
        slot=SLOT_KEY_CONCEPTS,
        title=SLOT_TITLES[SLOT_KEY_CONCEPTS],
        template="key_concepts.j2",
        build_context=_build,
        needs_module_corroboration=True,
    )
)
