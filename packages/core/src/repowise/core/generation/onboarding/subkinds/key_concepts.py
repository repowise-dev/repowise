"""Onboarding subkind: Key Concepts.

The abstractions and vocabulary this codebase uses — the mental model
needed to read the code. Not a glossary dump but a narrative that
identifies 4–6 load-bearing types/functions and the architectural
clusters they belong to.

Gate: at least 4 public symbols clear the PageRank P90 threshold. Below
that, the page would be guessing at "core concepts."
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_KEY_CONCEPTS, SLOT_TITLES

_GATE_MIN_CONCEPTS = 4
_PAGERANK_PERCENTILE = 0.90  # top 10% of file PageRank scores
_TOP_SYMBOLS = 10
_MAX_DECISION_RECORDS = 6
_MAX_COMMUNITY_LABELS = 6


@dataclass
class ConceptSymbol:
    """A candidate "core concept" symbol with the context needed to write
    about it without re-reading source."""

    name: str
    kind: str
    file_path: str
    docstring: str = ""
    pagerank: float = 0.0
    community_label: str = ""


@dataclass
class KeyConceptsContext:
    repo_name: str
    concept_symbols: list[ConceptSymbol] = field(default_factory=list)
    community_labels: list[str] = field(default_factory=list)
    decision_titles: list[str] = field(default_factory=list)


def _pagerank_threshold(pagerank: dict[str, float]) -> float:
    """Return the file-level PageRank cutoff at the configured percentile.

    The percentile is tightened to whichever yields the *more permissive*
    cutoff between the configured P90 and "top N files" (where N is the
    gate minimum). On a 5-file fixture pure P90 would only admit one
    file — too tight to express the gate's intent.
    """
    scores = sorted(pagerank.values(), reverse=True)
    if not scores:
        return 0.0
    idx_percentile = int(len(scores) * (1.0 - _PAGERANK_PERCENTILE))
    # Allow at least the top N files in (N-1 is the highest index that
    # should still pass the threshold). Higher index = lower score =
    # more permissive.
    idx_min_admission = _GATE_MIN_CONCEPTS - 1
    idx = max(idx_percentile, idx_min_admission)
    return scores[min(idx, len(scores) - 1)]


def _resolve_community_labels(graph_builder: Any) -> dict[int, str]:
    labels: dict[int, str] = {}
    try:
        info = graph_builder.community_info() or {}
        items = info.items() if hasattr(info, "items") else ()
        for cid, ci in items:
            label = getattr(ci, "label", "") or ""
            if label:
                labels[int(cid)] = label
    except Exception:
        pass
    return labels


def _build(signals: OnboardingSignals) -> KeyConceptsContext | None:
    threshold = _pagerank_threshold(signals.pagerank)
    if threshold <= 0.0:
        return None

    labels_by_cid = _resolve_community_labels(signals.graph_builder)

    # Score: file PageRank lifts every public symbol it contains. Symbol
    # frequency (defined more than once) acts as a tiebreaker, modelling
    # the heuristic that re-implemented concepts tend to be foundational.
    name_freq: Counter[str] = Counter()
    for pf in signals.parsed_files:
        for sym in pf.symbols:
            if getattr(sym, "visibility", "public") == "public":
                name_freq[sym.name] += 1

    candidates: list[ConceptSymbol] = []
    for pf in signals.parsed_files:
        path = pf.file_info.path
        pr = signals.pagerank.get(path, 0.0)
        if pr < threshold:
            continue
        cid = signals.community.get(path)
        community_label = labels_by_cid.get(int(cid)) if cid is not None else ""
        for sym in pf.symbols:
            if getattr(sym, "visibility", "public") != "public":
                continue
            # Skip near-noise: tiny utility functions and lowercase-only names
            # that almost never represent real concepts.
            if len(sym.name) <= 2:
                continue
            candidates.append(
                ConceptSymbol(
                    name=sym.name,
                    kind=str(getattr(sym, "kind", "symbol")),
                    file_path=path,
                    docstring=(getattr(sym, "docstring", "") or "").strip()[:300],
                    pagerank=pr,
                    community_label=community_label or "",
                )
            )

    if len(candidates) < _GATE_MIN_CONCEPTS:
        return None

    # Rank by (pagerank, name_frequency, has_docstring) — docstrings often
    # mark the deliberately-public surface.
    candidates.sort(
        key=lambda c: (
            c.pagerank,
            name_freq.get(c.name, 0),
            1 if c.docstring else 0,
        ),
        reverse=True,
    )

    # De-duplicate by symbol name to avoid the same concept appearing
    # multiple times when re-exported from several files.
    seen: set[str] = set()
    concept_symbols: list[ConceptSymbol] = []
    for c in candidates:
        if c.name in seen:
            continue
        seen.add(c.name)
        concept_symbols.append(c)
        if len(concept_symbols) >= _TOP_SYMBOLS:
            break

    if len(concept_symbols) < _GATE_MIN_CONCEPTS:
        return None

    community_labels = sorted(set(labels_by_cid.values()))[:_MAX_COMMUNITY_LABELS]
    decision_titles = [
        str(d.get("title", "")).strip()
        for d in signals.decisions_all[:_MAX_DECISION_RECORDS]
        if d.get("title")
    ]

    return KeyConceptsContext(
        repo_name=signals.repo_name,
        concept_symbols=concept_symbols,
        community_labels=community_labels,
        decision_titles=decision_titles,
    )


register(
    SubkindSpec(
        slot=SLOT_KEY_CONCEPTS,
        title=SLOT_TITLES[SLOT_KEY_CONCEPTS],
        template="key_concepts.j2",
        build_context=_build,
    )
)
