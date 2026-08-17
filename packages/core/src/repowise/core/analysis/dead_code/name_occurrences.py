"""Did we actually look — the knowledge term behind a dead-code confidence.

Every other input to a dead-code confidence scores *how strong the evidence
for deadness is*. None of them asks the second question a confidence has to
answer before a user can act on it without checking: **would a use have been
visible to us at all?**

``_detect_unused_exports`` promotes a finding to the top of the scale when the
defining file has importers — the argument being that our import graph
demonstrably works for this file, so the symbol's absence from every importer's
imported names means something. That argument assumes *using a symbol requires
importing it*. It holds in Python and TypeScript. It is false for:

* a same-package Kotlin or Go reference,
* a same-translation-unit C++ type,
* an intra-crate Rust path,
* a C# or Swift member of the same module,

where a use needs no import at all, so the absence of an import edge carries no
information about the symbol. The same blind spot swallows a use written
through an aliased import, an attribute call on an imported module, or a
handler named by string from infrastructure config.

This module supplies the missing term, and it is deliberately the weakest
possible form of it: **does the repository write this name anywhere other than
the declaration itself?** If it does, we have not looked everywhere, and the
finding is capped to the review tier with the file that said so. If it does
not, the original confidence stands.

Two properties make that safe to apply to every finding rather than to a
hand-picked language list:

* **It only ever suppresses.** A name written only in a comment counts as a
  use. That is a real ceiling, not an oversight, and it is why this can cost
  recall and can never invent a false negative in the other direction.
* **It never removes a finding.** The cap lands exactly on the default
  ``min_confidence``, so a capped finding is still reported. What it loses is
  the claim, not its place in the report.

The alternative considered and not taken was a per-language table of "does
usage require an import here". It is cheaper, but it is a list someone has to
keep true as languages are added, and it answers the question by assertion
where this answers it from the repository in front of us.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .models import DeadCodeFindingData, DeadCodeKind
from .risk_factors import RISK_CAP_CONFIDENCE

#: Identifier shape, matched over raw bytes so nothing has to be decoded — the
#: scan covers every indexed file, and decoding them all would dominate it.
#: ASCII-only is deliberate: a non-ASCII identifier that fails to match can
#: only under-suppress, which is the safe direction.
IDENTIFIER_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,}")

#: Shortest name this check can answer for. :data:`IDENTIFIER_RE` needs three
#: characters, so a two-character name matches nowhere — including on its own
#: declaration. Reading that as "the name appears nowhere" would turn the
#: strongest verdict into the least reliable one, so a name this short is
#: treated as a question we cannot ask rather than as an answer.
MIN_ANSWERABLE_NAME_LEN = 3


class _Answer(Enum):
    """What the search actually established. Four outcomes, kept apart.

    Only :data:`ABSENT` leaves a finding alone. The other three all cap it,
    but for reasons a reader needs told apart: a use was found, or the search
    could not be run, or it ran and only the declaration's own extent was
    unknown. Collapsing them into "not verified" is what makes an evidence
    line say something that did not happen.
    """

    ABSENT = "absent"
    USED = "used"
    NOT_SEARCHABLE = "not_searchable"
    SPAN_UNKNOWN = "span_unknown"


@dataclass(frozen=True)
class _Verdict:
    answer: _Answer
    used_at: str | None = None


_NOT_SEARCHABLE = _Verdict(_Answer.NOT_SEARCHABLE)
_SPAN_UNKNOWN = _Verdict(_Answer.SPAN_UNKNOWN)
_ABSENT = _Verdict(_Answer.ABSENT)


def occurrence_files(
    source_map: dict[str, bytes], names: set[bytes]
) -> dict[bytes, set[str]]:
    """For each name in *names*, the indexed files whose source writes it.

    One pass over the whole indexed source. The candidate names are known
    before the scan starts, so the returned map only ever holds the few names
    some finding actually asks about rather than every identifier in the
    repository — which is what keeps this affordable on a large tree.
    """
    found: dict[bytes, set[str]] = {}
    if not names:
        return found
    for path, blob in source_map.items():
        for match in IDENTIFIER_RE.finditer(blob):
            token = match.group()
            if token in names:
                found.setdefault(token, set()).add(path)
    return found


def _uses_in_own_file(
    source_map: dict[str, bytes],
    path: str,
    findings: list[DeadCodeFindingData],
) -> dict[int, _Verdict]:
    """Where each of *findings* is named in its own file outside its own span.

    Reached only for a name no other file mentions — the same-translation-unit
    shape, where a C++ helper struct used inside the very function below it is
    the use the import graph cannot see. A recursive call sits *inside* the
    declaration and correctly does not count; a doc comment above it sits
    outside and does, which is this module's stated textual ceiling.

    Two things make the line arithmetic exact rather than nearly right:

    * The split is on ``\\n`` alone. ``bytes.splitlines`` also breaks on a bare
      ``\\r`` and on the form-feed family, which the parser's row counter does
      not — so a lone ``\\r`` inside a string literal (a progress-bar ``print``
      is the common one) would shift every line after it and make a symbol's
      own recursive call land outside its recorded span.
    * A span is excluded for *every* candidate sharing the name in this file,
      not only for the one being judged. Overloads share a name and each
      declaration would otherwise read as a use of its sibling.
    """
    spanned = [
        f for f in findings if f.start_line is not None and f.end_line is not None
    ]
    out: dict[int, _Verdict] = {
        id(f): (_ABSENT if f in spanned else _SPAN_UNKNOWN) for f in findings
    }
    blob = source_map.get(path)
    if blob is None or not spanned:
        return out

    wanted: dict[bytes, list[DeadCodeFindingData]] = {}
    declarations: dict[bytes, list[tuple[int, int]]] = {}
    for finding in spanned:
        token = _token(finding.symbol_name)
        wanted.setdefault(token, []).append(finding)
        declarations.setdefault(token, []).append(
            (finding.start_line, finding.end_line)
        )

    for lineno, line in enumerate(blob.split(b"\n"), start=1):
        for match in IDENTIFIER_RE.finditer(line):
            token = match.group()
            if any(start <= lineno <= end for start, end in declarations.get(token, ())):
                continue
            for finding in wanted.get(token, ()):
                if out[id(finding)].answer is _Answer.USED:
                    continue
                out[id(finding)] = _Verdict(_Answer.USED, f"{path}:{lineno}")
    return out


def _token(name: str) -> bytes:
    """The searchable form of *name*, or empty when there is not one.

    ``encode(errors="ignore")`` drops non-ASCII characters rather than
    failing, which would turn ``café`` into a search for ``caf`` and let an
    unrelated ``caf`` elsewhere read as a use. A name that does not survive
    the round trip has no searchable form at all and is refused here, so the
    length rule below is applied to what will actually be searched for rather
    than to what the symbol is called.
    """
    encoded = name.encode("ascii", "ignore")
    if encoded.decode("ascii") != name or len(encoded) < MIN_ANSWERABLE_NAME_LEN:
        return b""
    return encoded


def _verdicts(
    source_map: dict[str, bytes], candidates: list[DeadCodeFindingData]
) -> dict[int, _Verdict]:
    """One verdict per candidate, from one repo-wide scan plus targeted reads."""
    # Built from the candidates alone, which is what keeps a whole-repo scan
    # affordable: the index only ever holds the names some finding asks about.
    answerable = {_token(f.symbol_name) for f in candidates} - {b""}
    occurrences = occurrence_files(source_map, answerable)

    out: dict[int, _Verdict] = {}
    # A name occurring in no file but its own needs the declaration's span
    # excluded before the question is answered at all. Those are gathered here
    # and read per file below, so each file is walked once however many of its
    # symbols are candidates.
    same_file_only: dict[str, list[DeadCodeFindingData]] = {}

    for finding in candidates:
        token = _token(finding.symbol_name)
        if not token:
            out[id(finding)] = _NOT_SEARCHABLE
            continue
        files = occurrences.get(token, set())
        elsewhere = sorted(files - {finding.file_path})
        if elsewhere:
            out[id(finding)] = _Verdict(_Answer.USED, elsewhere[0])
        elif finding.file_path in files:
            same_file_only.setdefault(finding.file_path, []).append(finding)
        else:
            # The scan cannot see the declaration it is standing on, so this
            # file was not among those searched and nothing about it was
            # established either way.
            out[id(finding)] = _NOT_SEARCHABLE

    for path, pending in same_file_only.items():
        out.update(_uses_in_own_file(source_map, path, pending))
    return out


def clamp_unverified_absence(
    findings: list[DeadCodeFindingData], source_map: dict[str, bytes]
) -> list[DeadCodeFindingData]:
    """Cap findings whose symbol the repository names somewhere else.

    Mutates in place and returns the same list, matching the sibling clamp in
    the analyzer. Never raises a confidence and never drops a finding.

    Scoped to unused *exports*, the one pass that promotes on import absence.
    Unused internals already sit below the threshold and never claim to be
    safe, and a whole-file finding would have to match on the path stem — the
    broad-word shape ("index", "main", "utils") that the unindexed clamp and
    the risk-factor token lists both refuse for being unable to tell a mention
    from a coincidence.

    With no source access there is no knowledge to add, so the pass declines
    rather than guessing in either direction.
    """
    if not source_map:
        return findings

    candidates = [
        f
        for f in findings
        if f.kind == DeadCodeKind.UNUSED_EXPORT
        and f.symbol_name
        and f.confidence > RISK_CAP_CONFIDENCE
    ]
    if not candidates:
        return findings

    verdicts = _verdicts(source_map, candidates)
    for finding in candidates:
        verdict = verdicts.get(id(finding), _NOT_SEARCHABLE)
        if verdict.answer is _Answer.ABSENT:
            continue
        name = finding.symbol_name
        # ``reason`` carries this, not only ``evidence``. The unchanged reason
        # asserts "has no importers" as the ground for the finding, which is
        # the very inference this check has just declined to make — and it is
        # the field every surface renders, where the evidence list reaches
        # only the JSON ones.
        if verdict.answer is _Answer.USED:
            finding.reason = f"'{name}' is not imported, but is named elsewhere in the repo"
            detail = f"is written at {verdict.used_at}"
        elif verdict.answer is _Answer.SPAN_UNKNOWN:
            finding.reason = f"'{name}' is not imported, and its own file could not be checked"
            detail = "is named in its own file, whose declaration could not be bounded"
        else:
            finding.reason = f"'{name}' is not imported, and its use could not be checked"
            detail = "could not be searched for across the repository"
        finding.confidence = min(finding.confidence, RISK_CAP_CONFIDENCE)
        finding.safe_to_delete = False
        finding.evidence.append(
            f"'{name}' {detail}, so the absence of an import does not establish disuse"
        )
    return findings
