"""Data model for documentation drift findings.

Shaped after dead code (``analysis/dead_code/models.py``) rather than health:
a drift finding is a claim about *existence* carried by a continuous
``confidence``, not an impact ranked by severity. Health's severity-first shape
expresses "how much does this hurt", which is the wrong axis for "is this
sentence still true".

As in dead code, the models carry no serializer. Each consumer (CLI, CRUD,
later surfaces) builds the dict it needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DriftKind(StrEnum):
    """The reference classes this detector ships.

    ``SYMBOL`` flags only a token git proves was a symbol definition when its
    line was written and is defined nowhere now (:mod:`.symbols`); backticks
    alone mean "literal", and flagging on shape was 55-69% noise.
    """

    PATH = "path"
    LINK = "link"
    ANCHOR = "anchor"
    COMMAND = "command"
    SYMBOL = "symbol"


class DriftVerdict(StrEnum):
    """The four answers a resolver may give about one reference.

    ``UNCHECKABLE`` is first-class and is the whole difference between a
    detector and a noise generator: 684 of 903 path references on this
    repository are uncheckable, and 100% of the probe's first-run false
    positives came from reporting them as drift. ``AMBIGUOUS`` is likewise
    never collapsed into ``MISSING`` --- a reference matching several
    candidates is not evidence of drift.
    """

    RESOLVED = "resolved"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    UNCHECKABLE = "uncheckable"


@dataclass(frozen=True)
class DocReference:
    """One checkable assertion a document makes about the repository.

    Frozen and hashable so the extractor can deduplicate without a side table.
    Every extractor yields this same shape, which is what keeps the resolver
    class-agnostic and lets a fifth class be added without touching the other
    four.
    """

    kind: DriftKind
    raw: str
    """The reference exactly as written, for display."""
    target: str
    """Normalized lookup key. For ``ANCHOR`` this is ``"<doc path>#<fragment>"``."""
    doc_path: str
    """Repo-relative POSIX path of the document making the assertion."""
    line: int
    """1-indexed line within the document."""
    context: str
    """The source line, trimmed, so a reader can judge the finding without
    opening the file."""
    section: str = ""
    """The enclosing heading trail, joined by " > ". Used to tell prose that
    *describes* the repository from prose that *teaches* the reader to add to
    it, which is a confidence tier rather than an exclusion."""
    column: int = field(default=-1, compare=False)
    """0-based offset of ``raw`` in the untrimmed line, or -1 when unknown.
    Outside equality, so one reference repeated on a line still dedupes."""


@dataclass
class DocDriftFindingData:
    """A document assertion the repository no longer satisfies."""

    kind: DriftKind
    file_path: str
    """The *document* that is wrong, not the target it names. This is the file
    a reader has to edit, so it is the one the finding is filed against."""
    line_number: int
    target: str
    """What the document claims exists."""
    confidence: float
    reason: str
    """One human sentence."""
    origin: str
    """Which named resolution strategy produced this, from
    :data:`~.constants.DriftOrigin`. Every origin carries a fixed confidence,
    so the origin distribution and the confidence histogram are two views of
    the same data."""
    evidence: list[str]
    """Lines a reader can check for themselves."""
    raw: str = ""
    context: str = ""
    suggestion: str = ""
    """A likely replacement for ``target``, or empty. Evidence for the reader,
    never applied: this detector does not rewrite documents."""
    suggestion_basis: str = ""
    """How ``suggestion`` was found, from :data:`~.constants.SuggestionBasis`."""
    suggested_line: str = ""
    """The whole document line with ``suggestion`` in place of every copy of
    the reference, or empty. Only a run that read the document has it."""
    suggestion_columns: tuple[tuple[int, int], ...] = ()
    """Each replaced span on the line: 1-based start and exclusive end, in
    UTF-16 code units."""
    defined_in: tuple[str, ...] = ()
    """``SYMBOL`` only: the files that defined the name when the line was
    written, and where they live now. Lets a change that edits one own it."""


@dataclass(frozen=True)
class ResolvedDocReference:
    """One reference that resolved: a document names this file, correctly.

    The complement of :class:`DocDriftFindingData`, and a deliberately weaker
    claim than that one. A finding says a document is wrong; this says only
    that a document names a file the repository still has. Whether the prose
    around the name still *describes* the file is not something this detector
    can see, so no surface built on these rows may say "describes".

    Only ``RESOLVED`` references that name a concrete repository file become
    one of these. ``AMBIGUOUS`` is excluded because a basename match is not a
    resolution, and ``COMMAND`` because a make target is not a file.
    """

    doc_path: str
    """Repo-relative POSIX path of the document making the reference."""
    target_path: str
    """Repo-relative POSIX path of the file it resolved to."""
    kind: DriftKind
    line: int
    """1-indexed line within the document."""
    section: str = ""
    """The enclosing heading trail, so a reader can find the passage."""


@dataclass(frozen=True)
class SymbolScope:
    """Which stored ``SYMBOL`` findings a write replaces: every one in
    ``documents``, and elsewhere only the ``(document, target)`` pairs listed."""

    documents: frozenset[str] = frozenset()
    references: frozenset[tuple[str, str]] = frozenset()

    def covers(self, document: str, target: str) -> bool:
        return document in self.documents or (document, target) in self.references


@dataclass
class DocDriftReport:
    """The drift pass's output for one repository."""

    repo_id: str
    analyzed_at: datetime
    total_findings: int
    findings: list[DocDriftFindingData]
    confidence_summary: dict
    """``{"high": N, "medium": N, "low": N}`` over the post-cutoff findings."""
    documents_scanned: int
    references_checked: int
    verdict_summary: dict
    """``{"resolved": N, "missing": N, "ambiguous": N, "uncheckable": N}`` over
    every reference examined. Reported because the uncheckable count is the
    honest denominator: a detector that hides it is claiming coverage it does
    not have."""
    anchor_renderer: str = "github"
    """Which renderer's slug algorithm the anchor class used, or the renderer
    that caused it to stand down. See :mod:`~.renderer`."""
    hidden_below_threshold: int = 0
    suppressed: int = 0
    """References an inline ``repowise-drift-ignore`` marker silenced. Counted
    so a suppression is visible and never mistaken for a clean doc."""
    documents: frozenset[str] = field(default_factory=frozenset)
    """Every document this run actually read, of which ``documents_scanned`` is
    the count. A document that missed ``source_map`` (transient read failure, or
    markdown over :data:`~.constants.MAX_DOC_BYTES`) is absent, which is what
    stops a scoped write from deleting findings it never recomputed."""
    resolved_references: list[ResolvedDocReference] = field(default_factory=list)
    """Every reference that resolved to a real file, for the reverse view
    ("which documents mention this file"). Retained rather than recomputed,
    because the resolver already had each one. Scoped by
    ``authoritative_paths`` exactly as findings are."""
    symbol_scope: SymbolScope | None = field(default=None)
    """The ``SYMBOL`` findings this report speaks for; ``None`` means every one
    in ``authoritative_paths``. An incremental update re-resolves only the
    references it could have changed, and the write carries the rest forward."""
    authoritative_paths: frozenset[str] | None = field(default=None)
    """Document paths this report may speak for; ``None`` means all of them.
    Mirrors dead code's field of the same name so an incremental re-check can
    replace a subset of rows without deleting findings it did not recompute."""
