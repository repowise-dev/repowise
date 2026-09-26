"""Tunables and vocabularies for the documentation drift detector.

One module so the analyzer, the CRUD layer, the CLI and any later consumer
import the same numbers. Dead code learned this the hard way: its bucket
boundaries live in ``dead_code/risk_factors.py`` precisely because three
surfaces had already drifted on them
(``tests/unit/dead_code/test_confidence_parity.py``).
"""

from __future__ import annotations

import re
from typing import Literal, get_args

from repowise.core.ingestion.languages.registry import REGISTRY

# ---------------------------------------------------------------------------
# Confidence buckets
#
# Deliberately the same boundaries as dead code's ``SAFE_CONFIDENCE_THRESHOLD``
# / ``RISK_CAP_CONFIDENCE``, so "high" means the same thing to a reader looking
# at either report. Pinned by ``tests/unit/doc_drift/test_confidence_parity.py``
# rather than imported, because coupling this analysis to dead code's risk
# vocabulary would make a change to *its* deletion policy silently retune
# *this* detector.
# ---------------------------------------------------------------------------

HIGH_CONFIDENCE_THRESHOLD: float = 0.7
REVIEW_CONFIDENCE_THRESHOLD: float = 0.4


def confidence_tier(value: float) -> str:
    """The house tier one confidence falls in: ``high``, ``medium`` or ``low``.

    The only place the two thresholds are compared. A surface that wants to
    colour or group a single finding asks for its tier rather than re-deriving
    the boundaries, so a surface and the summary beside it cannot disagree
    about where "high" starts.
    """
    if value >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    if value >= REVIEW_CONFIDENCE_THRESHOLD:
        return "medium"
    return "low"


def bucket_confidences(values) -> dict:
    """Bucket an iterable of confidences into the house high/medium/low tiers.

    One implementation, called by the analyzer when it builds a report and by
    the CRUD layer when it re-derives the summary on read. They were two
    copies of the same three comprehensions; a shared function is cheaper than
    the test that would have had to pin them together.
    """
    counts = {"high": 0, "medium": 0, "low": 0}
    for value in values:
        counts[confidence_tier(value)] += 1
    return counts


#: What every surface must say alongside a finding count, so no consumer can
#: imply coverage or precision this detector does not have. Two facts, both
#: measured: 702 of this repository's 1,783 references are UNCHECKABLE by
#: design, and two of the 19 findings are known, deliberate false positives.
#: One string rather than one sentence per surface, for the reason
#: ``tests/unit/dead_code/test_confidence_parity.py`` exists.
DETECTION_BASIS: str = (
    "Covers only references this detector can resolve; uncheckable ones are "
    "neither counted nor reported. A finding names the document to edit, not "
    "the target it names, and is evidence to check rather than a proven defect."
)


#: :data:`DETECTION_BASIS`'s job for the reverse view, which makes a weaker
#: claim: a row says a document names a file that exists, not that it explains
#: it. Separate sentences, because the finding one applied to a reference count
#: describes a number nobody took.
REFERENCE_BASIS: str = (
    "Lists documents that name this file and still resolve to it. It does not "
    "claim they describe it, or that their prose is current. References this "
    "detector cannot resolve are not listed, so absence is not proof that no "
    "document mentions the file. A document reported as carrying drift has "
    "some assertion that no longer holds, which need not be about this file."
)


#: Why a drift surface could not answer. Shared because a client keys on these
#: strings, and a literal in two packages is how one gets renamed alone.
#: ``NO_TABLE`` is an index older than the table; ``NOT_COMPUTED`` is a table
#: that exists and was never filled, which must not render as a clean tree;
#: ``READ_FAILED`` is anything else, since "your index is old" is wrong advice
#: for a transient failure.
UNAVAILABLE_NO_TABLE: str = "index_predates_doc_drift"
UNAVAILABLE_NOT_COMPUTED: str = "not_computed"
UNAVAILABLE_READ_FAILED: str = "drift_read_failed"


#: Default ``--min-confidence`` cutoff. Matches the lowest shipping origin's
#: tier so nothing is hidden by default; a caller that wants only the
#: near-certain findings raises it to ``HIGH_CONFIDENCE_THRESHOLD``.
DEFAULT_MIN_CONFIDENCE: float = REVIEW_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Resolution origins
#
# The ``ResolutionOrigin`` pattern from ``ingestion/models.py:407``: one named
# origin per way a verdict was reached, each carrying a fixed confidence. Phase
# 1 justified this over the coarser ``measured | inferred`` two-tier flag
# because the classes have measurably different precision (anchor 3/3, path
# 11/12) and because the one path false positive has a *nameable* shape --- an
# invented example inside a contributor guide --- which is a tier, not a rule.
#
# Confidences live in ``ORIGIN_CONFIDENCE`` below rather than in trailing
# comments, so the number a finding carries and the number documented here
# cannot disagree. A parity test asserts the two vocabularies stay in step.
# ---------------------------------------------------------------------------

DriftOrigin = Literal[
    "path_no_candidate",
    "path_no_candidate_in_guide",
    "anchor_no_heading",
    "command_no_target",
]

ORIGIN_CONFIDENCE: dict[str, float] = {
    # An anchored path whose basename appears nowhere in the tree. The bulk of
    # Phase 1's real findings (11/12), and the single most common drift shape:
    # a module became a package and the prose still names the ``.py``.
    "path_no_candidate": 0.90,
    # The same evidence, inside a document that teaches rather than describes.
    # Phase 1's only path false positive was ``languages/specs/mylang.py`` in a
    # guide on how to add a language: an invented path, correctly shaped, that
    # was never meant to exist. One tier down, not excluded --- a how-to guide
    # naming a real file that moved is still drift.
    "path_no_candidate_in_guide": 0.50,
    # A heading that was renamed with the inbound link left behind. 3/3 real on
    # this repo, and a class no linter catches: each document is internally
    # valid and the target file exists. Only the pair is wrong. Only ever
    # reached when the renderer is known to slug GitHub-style.
    "anchor_no_heading": 0.95,
    # A ``make``/``npm run`` target, written inside an inline code span, that
    # the manifest in the tree does not declare.
    "command_no_target": 0.85,
}

#: Every origin in the ``DriftOrigin`` vocabulary, for parity checks.
DRIFT_ORIGIN_VALUES: frozenset[str] = frozenset(get_args(DriftOrigin))


# ---------------------------------------------------------------------------
# Document selection
# ---------------------------------------------------------------------------

#: Extensions this detector can read, taken from the markdown ``LanguageSpec``
#: rather than restated, so the set of files the traverser calls markdown and
#: the set this detector scans cannot diverge.
#:
#: ``.rst`` and ``.adoc`` are deliberately outside this: flask was in the Phase
#: 1 corpus and contributed nothing because its docs are reStructuredText.
#: Those trees are beyond this detector's reach, and pretending otherwise would
#: mean shipping an untested extractor.
MARKDOWN_SUFFIXES: frozenset[str] = REGISTRY.get("markdown").extensions

#: Path segments holding code this repository did not write. Test and fixture
#: trees are NOT listed here: they are recognised by
#: :func:`repowise.core.test_paths.is_test_related_path`, the same helper that
#: stamps ``FileInfo.is_test`` at ingestion, so this detector and the rest of
#: the engine cannot disagree about what a test is. Phase 2 found why that
#: matters: fastapi keeps deliberately-broken markdown at
#: ``scripts/tests/test_translation_fixer/.../data/*.md`` as input fixtures for
#: its own link fixer, and a hand-written directory list did not recognise the
#: shape.
VENDORED_DIR_PARTS: frozenset[str] = frozenset(
    {
        "node_modules",
        "vendor",
        "third_party",
        "site-packages",
        ".venv",
        "venv",
        "dist",
        "build",
    }
)

# A changelog is a historical record. Every entry describes the tree as it was
# at that release, so a reference that no longer resolves is the document doing
# its job; checking one is a category error.
#
# Matched on the STEM AS A PATTERN, never an exact filename list. Phase 1's
# first fixture was named ``CHANGELOG.md``, which silently failed on fastapi's
# ``release-notes.md`` --- and all 17 of that repo's path findings came from it.
HISTORICAL_STEM_RE = re.compile(
    r"^(changelog|changes|history|news|release[-_]?notes?|releases|whatsnew|"
    r"what[-_]s[-_]new|migration[-_]?guide|upgrading)$",
    re.I,
)

# Documents that teach rather than describe. A path in one of these is more
# likely to be an illustration the author invented, so an unresolved reference
# here is stamped with the lower ``path_no_candidate_in_guide`` tier.
GUIDE_STEM_RE = re.compile(
    r"(contributing|tutorial|guide|guides|how[-_]?to|howto|getting[-_]?started|"
    r"quickstart|walkthrough|cookbook|recipes|example|examples|template)",
    re.I,
)

#: Ceiling for a single document, mirroring the 500KB ``max_file_size_kb``
#: that already applies to non-AST files. A document larger than this is not
#: prose anyone maintains.
MAX_DOC_BYTES: int = 500 * 1024
