"""Did we actually look â€” the knowledge term behind a dead-code confidence.

Every other input to a dead-code confidence scores *how strong the evidence
for deadness is*. None of them asks the second question a confidence has to
answer before a user can act on it without checking: **would a use have been
visible to us at all?**

``_detect_unused_exports`` promotes a finding to the top of the scale when the
defining file has importers â€” the argument being that our import graph
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

import posixpath
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from .constants import is_runner_file, is_tool_config
from .entry_shape import export_shape
from .models import DeadCodeFindingData, DeadCodeKind
from .risk_factors import RISK_CAP_CONFIDENCE

#: Identifier shape, matched over raw bytes so nothing has to be decoded â€” the
#: scan covers every indexed file, and decoding them all would dominate it.
#: ASCII-only is deliberate: a non-ASCII identifier that fails to match can
#: only under-suppress, which is the safe direction.
IDENTIFIER_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]{2,}")

#: Shortest name this check can answer for. :data:`IDENTIFIER_RE` needs three
#: characters, so a two-character name matches nowhere â€” including on its own
#: declaration. Reading that as "the name appears nowhere" would turn the
#: strongest verdict into the least reliable one, so a name this short is
#: treated as a question we cannot ask rather than as an answer.
MIN_ANSWERABLE_NAME_LEN = 3

#: Where an unused internal lands when its own file could not be searched for
#: it: the declaration's extent is unknown, or the name is one the scan cannot
#: see. Below :data:`RISK_CAP_CONFIDENCE`, so the default report hides it rather
#: than showing a finding nothing has checked.
UNVERIFIED_INTERNAL_CONFIDENCE = 0.3


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
    repository â€” which is what keeps this affordable on a large tree.
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

    Reached only for a name no other file mentions â€” the same-translation-unit
    shape, where a C++ helper struct used inside the very function below it is
    the use the import graph cannot see. A recursive call sits *inside* the
    declaration and correctly does not count; a doc comment above it sits
    outside and does, which is this module's stated textual ceiling.

    Two things make the line arithmetic exact rather than nearly right:

    * The split is on ``\\n`` alone. ``bytes.splitlines`` also breaks on a bare
      ``\\r`` and on the form-feed family, which the parser's row counter does
      not â€” so a lone ``\\r`` inside a string literal (a progress-bar ``print``
      is the common one) would shift every line after it and make a symbol's
      own recursive call land outside its recorded span.
    * A sibling declaration's *header line* is excluded as well as the judged
      symbol's own span. Overloads share a name, so each declaration would
      otherwise read as a use of the other. Only the header, never the sibling's
      whole body: a span can enclose an unrelated same-named symbol's real call
      site, and excluding the body would drop that use and leave a live symbol
      claiming the top tier.
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
    headers: dict[bytes, set[int]] = {}
    for finding in spanned:
        token = _token(finding.symbol_name)
        wanted.setdefault(token, []).append(finding)
        headers.setdefault(token, set()).add(finding.start_line)

    for lineno, line in enumerate(blob.split(b"\n"), start=1):
        for match in IDENTIFIER_RE.finditer(line):
            token = match.group()
            for finding in wanted.get(token, ()):
                if out[id(finding)].answer is _Answer.USED:
                    continue
                if finding.start_line <= lineno <= finding.end_line:
                    continue
                if lineno in headers[token]:
                    continue  # a sibling declaration of the same name
                out[id(finding)] = _Verdict(_Answer.USED, f"{path}:{lineno}")
    return out


def _token(name: str) -> bytes:
    """The searchable form of *name*, or empty when the scan cannot find it.

    The one rule is that the result must be something :data:`IDENTIFIER_RE`
    would actually match, because a token the scan cannot produce is absent
    from every file including the one that declares it â€” and reading that as
    "the name appears nowhere" would turn the strongest verdict into the least
    reliable one. Two shapes fail it, for different reasons:

    * a non-ASCII name, since ``encode(errors="ignore")`` drops characters
      rather than failing and would search for ``caf`` on behalf of ``cafÃ©``,
      letting an unrelated ``caf`` elsewhere read as a use;
    * a name carrying a character the identifier shape does not admit, such as
      the ``$`` in a JVM or JavaScript synthetic name, which the scan would
      only ever see as two shorter tokens.
    """
    encoded = name.encode("ascii", "ignore")
    if encoded.decode("ascii") != name:
        return b""
    if len(encoded) < MIN_ANSWERABLE_NAME_LEN or not IDENTIFIER_RE.fullmatch(encoded):
        return b""
    return encoded


def _is_own_type_sibling(occurrence: str, declaring: str) -> bool:
    """True when *occurrence* is *declaring*'s own generated declaration file.

    A ``.d.ts`` beside ``runtime.js`` restates that module's exports as types.
    It declares the same names a second time and calls none of them, so
    counting it as a use makes every symbol in a generated binding module look
    alive â€” measured as the single largest source of lost true positives on
    this corpus.

    Deliberately narrow: only the same directory and the same stem. A ``.d.ts``
    naming a symbol from *elsewhere* really is referring to it, and stays a use.
    """
    if not occurrence.endswith(".d.ts"):
        return False
    stem = occurrence[: -len(".d.ts")]
    base = declaring.rsplit(".", 1)[0]
    return stem == base


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
        elsewhere = sorted(
            f
            for f in files - {finding.file_path}
            if not _is_own_type_sibling(f, finding.file_path)
        )
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
    Unused internals are settled by :func:`drop_internals_used_in_own_file`,
    which can drop rather than cap because a private name has only its own file
    to be used in. A whole-file finding is matched by path, never by its stem,
    in :func:`clamp_path_mentions`.

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
    # Keyed by identity, so a findings list that happens to hold one object
    # twice does not collect the same evidence line twice.
    for finding in {id(f): f for f in candidates}.values():
        verdict = verdicts.get(id(finding), _NOT_SEARCHABLE)
        if verdict.answer is _Answer.ABSENT:
            continue
        name = finding.symbol_name
        # ``reason`` carries this, not only ``evidence``. The unchanged reason
        # asserts "has no importers" as the ground for the finding, which is
        # the very inference this check has just declined to make â€” and it is
        # the field every surface renders, where the evidence list reaches
        # only the JSON ones.
        if verdict.answer is _Answer.USED:
            finding.reason = f"'{name}' is not imported, but is named elsewhere in the repo"
            detail = f"is written at {verdict.used_at}"
        else:
            # Both remaining answers mean the same thing to a reader â€” we did
            # not establish an absence â€” so they share a reason and differ only
            # in the evidence line, which is where the distinction is useful.
            finding.reason = f"'{name}' is not imported, and its use could not be verified"
            detail = (
                "is named in its own file, whose declaration could not be bounded"
                if verdict.answer is _Answer.SPAN_UNKNOWN
                else "could not be searched for across the repository"
            )
        finding.confidence = min(finding.confidence, RISK_CAP_CONFIDENCE)
        finding.safe_to_delete = False
        finding.evidence.append(
            f"'{name}' {detail}, so the absence of an import does not establish disuse"
        )
    return findings


def drop_internals_used_in_own_file(
    findings: list[DeadCodeFindingData], source_map: dict[str, bytes]
) -> list[DeadCodeFindingData]:
    """Drop unused internals their own file names outside their declaration.

    The graph carries calls and a few reference kinds, but not every way a
    private name is used: a module constant read in a function below it, a
    handler passed as a value (``target=fn``, ``re.sub(pattern, fn, s)``), a
    type named in an annotation, a component written as JSX or as object
    shorthand. Each of those reached this pass as "not used anywhere". A
    private name can only be used in its own file, so a mention there outside
    the symbol's own span is a use and the finding is dropped, not capped.

    A recursive call sits inside the span and does not count, so a function
    only calling itself stays reported. A mention in a comment does count:
    that is this module's textual ceiling, and it only ever costs recall.

    When the file was read but the question could not be asked (no span, or a
    name the identifier scan cannot see), the finding stays but falls to
    :data:`UNVERIFIED_INTERNAL_CONFIDENCE`. A file with no source at all is
    left as it was: nothing was established either way.
    """
    by_file: dict[str, list[DeadCodeFindingData]] = {}
    for finding in findings:
        if (
            finding.kind is DeadCodeKind.UNUSED_INTERNAL
            and finding.symbol_name
            and finding.file_path in source_map
        ):
            by_file.setdefault(finding.file_path, []).append(finding)
    if not by_file:
        return findings

    verdicts: dict[int, _Verdict] = {}
    for path, pending in by_file.items():
        verdicts.update(_uses_in_own_file(source_map, path, pending))

    kept: list[DeadCodeFindingData] = []
    for finding in findings:
        verdict = verdicts.get(id(finding))
        if verdict is not None:
            if verdict.answer is _Answer.USED:
                continue
            if verdict.answer is _Answer.SPAN_UNKNOWN or not _token(finding.symbol_name):
                finding.confidence = min(finding.confidence, UNVERIFIED_INTERNAL_CONFIDENCE)
                finding.evidence.append(
                    f"'{finding.symbol_name}' could not be checked against the rest of "
                    "its own file, so its disuse is unverified"
                )
        kept.append(finding)
    return kept


#: A path-shaped run of bytes: what a manifest, build script or doc writes when
#: it names a file. It must hold a ``.`` or a ``/`` (every key does), so the
#: regex engine skips plain words instead of handing each one to Python.
#: Whether a run names a candidate is decided by the lookup.
_PATH_TOKEN_RE = re.compile(rb"[A-Za-z0-9_@~+\-]*[./][A-Za-z0-9_.@~+\-/]*")


def _path_keys(path: str) -> list[str]:
    """The spellings that name *path*: the repo-relative path, the path without
    its extension, and its ``dir/basename.ext`` tail.

    Never the bare stem. ``index``, ``main`` and ``utils`` name a file in every
    directory, and a stem cannot tell a mention from a coincidence. So the
    extension-less form is kept only while it still carries a directory, and a
    root-level file with no extension (a bare word) has no key at all.
    """
    keys = [path] if ("/" in path or "." in path) else []
    stem, dot, ext = path.rpartition(".")
    if dot and "/" in stem and "/" not in ext:
        keys.append(stem)
    parent, slash, base = path.rpartition("/")
    if slash:
        keys.append(f"{parent.rpartition('/')[2]}/{base}")
    return keys


def _token_spellings(token: str) -> list[str]:
    """What a path-shaped *token* could be naming.

    Every suffix that starts after a ``/``, so ``./src/a.ts``, ``../src/a.ts``
    and an absolute path all reach ``src/a.ts``; and each suffix without its
    extension, so a build output (``src/a.js``) names its source (``src/a.ts``)
    through the shared extension-less key.
    """
    token = token.strip(".")
    out: list[str] = []
    start = 0
    while True:
        suffix = token[start:]
        if suffix:
            out.append(suffix)
            stem, dot, ext = suffix.rpartition(".")
            if dot and stem and "/" not in ext:
                out.append(stem)
        slash = token.find("/", start)
        if slash < 0:
            return out
        start = slash + 1


def _files_naming(targets: set[str], source_map: dict[str, bytes]) -> dict[str, list[str]]:
    """For each of *targets* other files name by path, those files in path order."""
    wanted: dict[str, set[str]] = {}
    for target in targets:
        for key in _path_keys(target):
            wanted.setdefault(key, set()).add(target)
    # The last segment of every key, checked before any suffix work: nearly
    # every path-shaped token in a repository names none of the candidates.
    tails = {key.rpartition("/")[2] for key in wanted}

    named_in: dict[str, list[str]] = {}
    for path, blob in sorted(source_map.items()):
        base = path.rpartition("/")[0]
        for target in _targets_in(blob, wanted, tails, base) - {path}:
            named_in.setdefault(target, []).append(path)
    return named_in


def _targets_in(
    blob: bytes, wanted: dict[str, set[str]], tails: set[str], base: str
) -> set[str]:
    """The targets any path-shaped token of *blob*, a file in *base*, names."""
    return {
        target
        for match in _PATH_TOKEN_RE.finditer(blob)
        for target in _targets_named_by(match.group().decode("ascii"), wanted, tails, base)
    }


def _targets_named_by(
    token: str, wanted: dict[str, set[str]], tails: set[str], base: str
) -> set[str]:
    """The targets one path-shaped *token* names, through any of its spellings.

    A tool resolves a relative path against the directory of the file that
    writes it (``setupFiles: ["./vitest.setup.ts"]`` in ``src/vitest.config.ts``
    is ``src/vitest.setup.ts``), so that resolution is one more spelling.
    """
    tail = token.strip(".").rpartition("/")[2]
    if tail not in tails and tail.rpartition(".")[0] not in tails:
        return set()
    spellings = _token_spellings(token)
    if base and not token.startswith("/"):
        # Not through ``_token_spellings``: its dot strip would eat the
        # leading dot of a dot-directory (``.changeset/``).
        resolved = posixpath.normpath(f"{base}/{token}")
        spellings += [resolved, resolved.rpartition(".")[0]]
    return {target for spelling in spellings for target in wanted.get(spelling, ())}


#: What a file named by path can be used as: a file a runner loads, or a module
#: whose exports a tool config reads.
_PATH_LOADED_KINDS = frozenset({DeadCodeKind.UNREACHABLE_FILE, DeadCodeKind.UNUSED_EXPORT})


def clamp_path_mentions(
    findings: list[DeadCodeFindingData], source_map: dict[str, bytes]
) -> list[DeadCodeFindingData]:
    """Cap unreachable files another file names by path; drop those a runner names.

    "Nothing imports this" is not "nothing uses this". A build script reads a
    template by path, a JSON manifest lists example files, a doc links a
    script, ``package.json`` names a bin: each loads the file without an import
    edge, and such a file reported as deletion-ready breaks whatever reads it.
    A mention in a doc is not proof of use, so that finding is capped to the
    review tier. A CI workflow, build file, manifest or shell script runs or
    ships what it names (``python scripts/emit_sample_dsl.py`` in a workflow),
    so a file one of those names is dropped. A tool config that loads a module
    reads what the module exports by default (a changesets changelog module,
    a docs site's sidebars), so the unused exports of a default-exporting
    module a tool config names are dropped too; a setup file the config runs
    for its side effects keeps them.

    One scan over the indexed source. That already holds JSON, YAML, Markdown,
    shell and ``package.json``: each has a language spec, so ingestion reads
    it. A file's mention of itself is not a use. Returns a new list; never
    raises a confidence.
    """
    in_scope = [f for f in findings if f.kind in _PATH_LOADED_KINDS]
    if not source_map or not in_scope:
        return findings

    named_in = _files_naming({f.file_path for f in in_scope}, source_map)
    run = {id(f) for f in in_scope if _loaded(f, named_in.get(f.file_path, ()), source_map)}
    kept = [f for f in findings if id(f) not in run]
    for finding in kept:
        if finding.kind is DeadCodeKind.UNREACHABLE_FILE:
            _cap_named_by_path(finding, named_in.get(finding.file_path))
    return kept


def _loaded(
    finding: DeadCodeFindingData, namers: Iterable[str], source_map: dict[str, bytes]
) -> bool:
    """Whether *namers* load the file of *finding* in the way the finding denies."""
    if finding.kind is DeadCodeKind.UNREACHABLE_FILE:
        return any(map(is_runner_file, namers))
    blob = source_map.get(finding.file_path)
    return any(map(is_tool_config, namers)) and "default" in export_shape(
        finding.file_path, frozenset(), blob
    )


def _cap_named_by_path(finding: DeadCodeFindingData, namers: list[str] | None) -> None:
    """Cap *finding* to the review tier when a file names it by path."""
    if not namers or finding.confidence <= RISK_CAP_CONFIDENCE:
        return
    finding.confidence = RISK_CAP_CONFIDENCE
    finding.safe_to_delete = False
    finding.evidence.append(f"Named by path in {namers[0]}, which may load it without an import")
def _is_reference_assembly(path: str) -> bool:
    """A .NET reference-assembly source: ``src/libraries/X/ref/X.cs``."""
    return path.endswith(".cs") and "/ref/" in f"/{path}"


def _writers(
    source_map: dict[str, bytes], names: Mapping[int, frozenset[str]]
) -> dict[bytes, set[str]]:
    """For every searchable name in *names*, the files of *source_map* writing it."""
    tokens = {_token(name) for group in names.values() for name in group} - {b""}
    return occurrence_files(source_map, tokens)


def drop_reference_assembly_api(
    findings: list[DeadCodeFindingData],
    source_map: dict[str, bytes],
    type_names: Mapping[str, frozenset[str]],
) -> list[DeadCodeFindingData]:
    """Drop C# findings whose type a reference assembly lists.

    A ``ref/*.cs`` file is the compile-time surface of a .NET library: every
    type it names is public API, used by code outside the repository, so
    neither the type nor the file declaring it is dead. *type_names* maps an
    unreachable file to the types it declares. Returns a new list.
    """
    references = {p: b for p, b in source_map.items() if _is_reference_assembly(p)}
    names = {
        id(f): _type_names_of(f, type_names)
        for f in findings
        if f.file_path.endswith(".cs") and not _is_reference_assembly(f.file_path)
    }
    if not references or not names:
        return findings
    listed = _writers(references, names)
    return [
        f
        for f in findings
        if not any(_token(name) in listed for name in names.get(id(f), ()))
    ]


def clamp_named_types(
    findings: list[DeadCodeFindingData],
    source_map: dict[str, bytes],
    type_names: Mapping[str, frozenset[str]],
) -> list[DeadCodeFindingData]:
    """Cap unreachable files whose types another file names.

    The file-level counterpart of :func:`clamp_unverified_absence`. A Java or
    C# type is used from its own package or namespace without any import, so
    a file no edge reaches but whose type another file writes has not been
    shown unused. Capped to the review tier, never dropped: a name is not
    proof of use. Mutates in place and returns the same list.
    """
    candidates = [
        f
        for f in findings
        if f.kind is DeadCodeKind.UNREACHABLE_FILE
        and f.confidence > RISK_CAP_CONFIDENCE
        and type_names.get(f.file_path)
    ]
    if not candidates or not source_map:
        return findings
    names = {id(f): type_names[f.file_path] for f in candidates}
    writers = _writers(source_map, names)
    for finding in candidates:
        written = _first_writer(finding.file_path, names[id(finding)], writers)
        if written is None:
            continue
        path, name = written
        finding.confidence = min(finding.confidence, RISK_CAP_CONFIDENCE)
        finding.evidence.append(
            f"Its type '{name}' is written in {path}, which may use it without an import"
        )
    return findings


def _first_writer(
    file_path: str, names: frozenset[str], writers: dict[bytes, set[str]]
) -> tuple[str, str] | None:
    """The first ``(path, name)`` where another file writes one of *names*."""
    return min(
        (
            (path, name)
            for name in names
            for path in writers.get(_token(name), ())
            if path != file_path and not _is_own_type_sibling(path, file_path)
        ),
        default=None,
    )


def _type_names_of(
    finding: DeadCodeFindingData, type_names: Mapping[str, frozenset[str]]
) -> frozenset[str]:
    if finding.kind is DeadCodeKind.UNREACHABLE_FILE:
        return type_names.get(finding.file_path, frozenset())
    if finding.kind is DeadCodeKind.UNUSED_EXPORT and finding.symbol_name:
        return frozenset({finding.symbol_name})
    return frozenset()
