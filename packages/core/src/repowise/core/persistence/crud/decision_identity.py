"""A decision's derived id, and the status/kind rules re-extraction must respect."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from repowise.core.analysis.decisions.lifecycle import ARCHITECTURAL_KIND, DECISION_KINDS
from repowise.core.analysis.decisions.provenance import normalize_text

# Namespaced so a decision id can never collide with another kind of derived
# id, and versioned so a future change of the recipe is a visible new value
# rather than a silent reshuffle of every id in every store.
_ID_NAMESPACE = "repowise.decision.id.v2"

# The identity fields are joined with a separator none of them can contain,
# so no pair of distinct records can flatten to the same string.
_FIELD_SEP = "\x00"

# ``evidence_file`` is nullable and NULL is not the empty string here: the
# dedupe query treats them as different records, so the derivation must too.
_NULL_EVIDENCE_FILE = "\x01"

# The pinned quote is truncated before it is hashed. A quote long enough to
# reach this is a paragraph, and its opening already separates two decisions
# that share a file set; carrying the rest only widens the surface on which a
# re-mine can reword the identity out from under a reference somebody wrote.
_IDENTITY_QUOTE_CHARS = 200

# Statuses a human (or the evolution judge) set deliberately. Re-extraction
# must never walk one of these back to ``proposed``: a re-harvested source
# ties or beats the stored rank, so without this guard a reindex would flip
# confirmed decisions back into the review queue and resurrect dismissed ones
# (#751). ``dismissed`` is terminal: the row is kept purely as a tombstone so
# the same content never re-proposes.
_PROTECTED_STATUSES = frozenset({"active", "deprecated", "superseded", "dismissed"})


def _extraction_status(incoming: str) -> str:
    """The status extraction is allowed to write.

    Extraction proposes; acceptance promotes. An incoming ``active`` is a
    parsed ADR heading or an old code path's optimism, and neither is an
    acceptance event, so it lands as a proposal. A tracked ADR still reaches
    ``active`` — via :func:`_accept_from_tracked_artifact` below, which records
    the document as the accepter instead of leaving the promotion anonymous.
    """
    return "proposed" if incoming == "active" else incoming


def _merge_status(existing: str, incoming: str) -> str:
    """Resolve a re-extracted status against the stored one.

    A protected stored status wins over an incoming ``proposed``; anything else
    follows the incoming value, after :func:`_extraction_status` has taken
    authority out of extraction's hands.
    """
    incoming = _extraction_status(incoming)
    if incoming == "proposed" and existing in _PROTECTED_STATUSES:
        return existing
    return incoming


def _extraction_kind(incoming: str | None) -> str:
    """The kind extraction is allowed to write.

    Unrecognised falls to ``architectural`` rather than being stored: the
    vocabulary has no CHECK behind it, every reader tests ``== AGREEMENT_KIND``,
    and a junk value that merely happens to read as architectural everywhere is
    safe by luck. Normalising here makes it safe by construction.
    """
    return incoming if incoming in DECISION_KINDS else ARCHITECTURAL_KIND

def identity_quote_for(text: str | None) -> str:
    """The pinned form of a decision's quote, for identity only.

    Whitespace-collapsed, lowercased and truncated, so the identity survives a
    re-mine that reflows or recases the same sentence. The stored column keeps
    the verbatim text; only this derived form is hashed.
    """
    return normalize_text(text)[:_IDENTITY_QUOTE_CHARS]


def derive_decision_id(
    repository_id: str,
    title: str,
    *,
    source: str,
    evidence_file: str | None,
    affected_files: Iterable[str] = (),
    evidence_line: int | None = None,
    identity_quote: str = "",
    needs_split: bool = False,
) -> str:
    """The id a decision with this identity has, in every store, on every run.

    Keyed on the evidence rather than on the title. A title is the most
    volatile thing a decision carries: two extractions of the same choice word
    it differently and produce two records, and rewording one moves its id.
    Over the dogfood store, title identity collapses **zero** duplicate pairs
    while the files-plus-evidence-plus-quote key below collapses **120 pairs
    across 42 classes**, and loses none of what title identity caught.

    The quote is load-bearing, and is why the file set alone is not the key:
    two unrelated classes in that store share a file pair and are separated
    only by what was said. It is also the fragile part, because most records
    key on mined prose rather than on a committed span, so it is **pinned in
    the row at first capture and never re-derived**. A later extraction that
    rewords the same sentence then leaves the id where it is.

    A record with no evidence at all keeps its title, for the same reason the
    rest of them lose it: identity is the evidence, and a record that has
    none has no evidence identity to key on. Without this every scopeless,
    quoteless record in a repository derives one id and the second one to be
    written collides with the first. Two of the 357 records in the dogfood
    store are in that state, and the construction paths that supply nothing
    but a title are all in that state.

    A record flagged ``needs_split`` keeps its title in the key. A bundled
    claim shares its files and its evidence with the separate decisions it
    bundles, so evidence identity would fold all of them together and file two
    decisions under a third one's name. Holding the flagged record on title
    identity is what keeps it apart, and it is why the flag has to be set
    before this key ships. The guard is partial: it reaches the bundles a
    marker can see and no others, measured at 28 of 357 records held out for
    20 of 120 merges given up.

    ``source`` is deliberately **not** in the key. Two lanes that mined the
    same sentence out of the same files recorded one decision, not two, and
    the quote already separates two that merely share a scope.

    32 lowercase hex, because ``DecisionRecord.id`` is ``String(32)`` and so
    is every foreign key to it, so a truncated digest fits without a column
    change. ``evidence_file`` is NULL far more often than not, so it gets a
    sentinel no path can contain rather than collapsing into the empty string.

    Note the id follows the identity, so editing the scope or the pinned quote
    moves it. :mod:`decision_id_migration`, which runs at the head of every
    index, is what settles that, and it leaves an alias behind so an id
    already written down keeps resolving.
    """
    files = sorted({str(f) for f in affected_files if f})
    quote = identity_quote_for(identity_quote)
    grounded = bool(files) or evidence_file is not None or bool(quote)
    parts = (
        _ID_NAMESPACE,
        repository_id,
        json.dumps(files),
        json.dumps(
            [
                _NULL_EVIDENCE_FILE if evidence_file is None else evidence_file,
                evidence_line,
            ]
        ),
        quote,
        title if needs_split or not grounded else "",
        source if not grounded else "",
    )
    digest = hashlib.sha256(_FIELD_SEP.join(parts).encode("utf-8")).hexdigest()
    return digest[:32]
