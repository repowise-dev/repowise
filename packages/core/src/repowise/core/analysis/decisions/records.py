"""The records every decision source produces, and the errors a source raises."""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.analysis.decisions.lifecycle import ARCHITECTURAL_KIND


@dataclass
class ExtractedDecision:
    title: str
    context: str = ""
    decision: str = ""
    rationale: str = ""
    alternatives: list[str] = field(default_factory=list)
    consequences: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    #: The files the mining model named, before the commit's own file list
    #: validates them. Mining-time only: the commit miners intersect it into
    #: :attr:`affected_files` and clear it. ``[]`` binds the record to nothing;
    #: ``None`` (never asked, or no answer) falls back to the commit-wide scope.
    proposed_files: list[str] | None = None
    affected_modules: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "inline_marker"
    #: See :class:`~repowise.core.persistence.models.DecisionRecord.scope_basis`.
    #: Set by the commit-derived miners, which are the only ones that take a
    #: file list they did not choose per file.
    scope_basis: str = ""
    evidence_commits: list[str] = field(default_factory=list)
    evidence_file: str | None = None
    evidence_line: int | None = None
    confidence: float = 0.5
    status: str = "proposed"
    # Defaulted: only the session lane mines prose where an agreement about
    # conducting the work is stated.
    kind: str = ARCHITECTURAL_KIND
    # The claimed verbatim quote and the substring gate's verdict on it.
    source_quote: str = ""
    verification: str = "unverified"  # exact | fuzzy | unverified
    # Transient: the verbatim span this decision was drawn from, consumed by
    # the substring gate and cleared before persistence.
    source_text: str = ""
    # ``source`` alone cannot separate the two session lanes. Blank and False
    # mean the lane said nothing, not that it said no.
    lane: str = ""
    needs_split: bool = False
    # A source that measures its own conformance writes it; None means the
    # git-diff recompute owns it.
    staleness_score: float | None = None


class DecisionSourceError(RuntimeError):
    """Every batch of a decision source failed.

    Raised so :meth:`DecisionExtractor.extract_all` records the source as
    failed rather than empty. A source that loses *some* batches still
    returns what it has and only logs, because partial supply beats none.
    """


class EmptyModelResponseError(DecisionSourceError):
    """The model returned no body at all for one batch.

    Distinct from ``[]``, which is a real answer and the common one. Raised so
    the batch lands in :func:`_collect_batches` as a failure rather than as a
    source with nothing in it.
    """


@dataclass
class DecisionExtractionReport:
    total_found: int
    decisions: list[ExtractedDecision]
    by_source: dict[str, int]
    # {source name: error text} for sources that raised. A failed source
    # contributes zero decisions, so this is what tells an outage from an
    # empty repo.
    failures: dict[str, str] = field(default_factory=dict)
