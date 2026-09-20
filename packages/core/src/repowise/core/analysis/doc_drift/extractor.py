"""Extraction of checkable references from markdown prose.

Why this is a third implementation rather than a reuse of the two that exist
---------------------------------------------------------------------------
``generation/page_generator/validation.py:373`` and
``generation/interlinking.py:32`` both regex backtick-quoted refs out of
markdown, and the second says in a comment that it is reusing the first. A
shared extractor was evaluated for this third caller and rejected, because the
three ask different questions:

* ``validation`` checks *generated* prose against the single ``ParsedFile`` the
  page was written about, and its regex matches identifiers only --- it cannot
  express a path, having no ``/`` in its character class. It is a symbol
  checker, and the symbol class is exactly what Phase 1 killed.
* ``interlinking`` resolves refs to ``page_id``s and strips fences with one
  ``re.DOTALL`` pass over the whole document, which destroys line positions.
* This detector files **line-level** findings against **user-authored** prose,
  checked against the **whole tree**. It needs line numbers (the persistence
  key includes one), markdown links, URL fragments and inline commands --- three
  of its four classes are things neither of the others extracts at all.

Folding them together would mean one function with three incompatible regexes
and a mode flag. The honest answer is a third extractor that shares the
vocabulary (:mod:`.models`) rather than the code. Recorded here so the next
reader does not re-litigate it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.test_paths import is_test_related_path

from .constants import (
    GUIDE_STEM_RE,
    HISTORICAL_STEM_RE,
    MARKDOWN_SUFFIXES,
    VENDORED_DIR_PARTS,
)
from .models import DocReference, DriftKind

# ---------------------------------------------------------------------------
# Span handling
#
# Two kinds of span must never contribute a reference:
#
# 1. Fenced code blocks. Their paths and symbols are illustrative by
#    construction. The decision extractor already tracks fences for the same
#    reason (``decisions/extractor.py:475``).
# 2. Repowise's own generated blocks. ``editor_files/base.py:28`` wraps them in
#    START / END HTML comment markers, so they are skippable exactly. The
#    traverser deliberately does NOT drop generated markdown
#    (``traverser.py:804`` exempts passthrough languages from the check), so
#    this is ours to do.
#
# The START marker carries a trailing sentence and an em dash, so it is matched
# on its prefix rather than the full literal.
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
# Hyphens belong in the tag: ``ALL-CONTRIBUTORS-LIST:START`` is the commonest
# generated block in the wild, and it is a wall of avatar image links.
_GENERATED_START_RE = re.compile(r"<!--\s*[A-Z][A-Z0-9_-]*:START\b")
_GENERATED_END_RE = re.compile(r"<!--\s*[A-Z][A-Z0-9_-]*:END\b")


def prose_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(1-indexed line number, line)`` for prose lines only.

    Line numbers stay absolute across skipped spans, because a finding's line
    has to point at the real file.
    """
    in_fence = False
    fence_marker = ""
    in_generated = False
    # Split on newlines only. ``str.splitlines()`` also breaks on the
    # vertical tab, form feed, NEL and U+2028/U+2029, which git and every
    # editor count as ordinary characters. A document carrying one of those
    # (they arrive routinely by copy-paste from the web) would shift every
    # later line number, and ``line_number`` is both the persistence key and
    # the only thing that makes a finding actionable.
    for i, raw_line in enumerate(text.split("\n"), start=1):
        line = raw_line.rstrip("\r")

        m = _FENCE_RE.match(line)
        if m:
            marker = m.group(1)[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                # Only the marker that opened the fence can close it. A ``~~~``
                # inside a ``` block is content, and treating it as a close
                # inverts the state for the rest of the document.
                in_fence, fence_marker = False, ""
            continue

        if in_fence:
            # Checked before the generated-marker tests, not after. A document
            # that *shows* a START marker in an example --- this project's own
            # docs do --- would otherwise turn the skip on with no matching END
            # outside the fence, silently discarding the rest of the file.
            continue

        if _GENERATED_START_RE.search(line):
            # A START and its END on one line opens and closes nothing.
            in_generated = not _GENERATED_END_RE.search(line)
            continue
        if _GENERATED_END_RE.search(line):
            in_generated = False
            continue
        if in_generated:
            continue
        yield i, line


# ---------------------------------------------------------------------------
# Shared exclusions
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"^(https?|mailto|ftp|tel|data):", re.I)

# Tokens that are placeholders by convention. Flagging one means flagging the
# author's illustration.
_PLACEHOLDER_SEGMENTS = frozenset(
    {
        "path",
        "to",
        "your",
        "my",
        "some",
        "any",
        "example",
        "examples",
        "foo",
        "bar",
        "baz",
        "qux",
        "xxx",
        "yyy",
        "name",
        "project",
        "repo",
        "user",
        "username",
        "org",
        "owner",
        "dir",
        "folder",
    }
)

# Shell or templating metacharacters mean the token is a pattern, not a path.
_TEMPLATED_RE = re.compile(r"[{}$<>*?|\\\[\]()]|\.\.\.|\bTODO\b")

# Extensions that make a slashed token a path assertion rather than prose.
#
# Derived from the LanguageRegistry rather than hand-listed, following
# ``ingestion/git_indexer/_constants.py``. A hand-written list started 64
# extensions short of what the engine already recognised --- every markdown
# reference to a ``.lua``, ``.zig``, ``.jl`` or ``.kts`` file was dropped
# before resolution, so it never even reached the uncheckable count that makes
# this detector's coverage honest. It also disagreed with this module's own
# ``MARKDOWN_SUFFIXES``: ``.markdown`` was scannable as a document but
# unresolvable as a link target.
#
# The residue below is the genuinely unregistered remainder: data, asset and
# lockfile extensions that name no language.
_UNREGISTERED_EXTS = frozenset(
    {
        ".cfg", ".css", ".env", ".gif", ".ini", ".jpg", ".lock", ".png",
        ".ps1", ".rst", ".scss", ".svg", ".txt", ".xml",
    }
)
KNOWN_EXTS: frozenset[str] = frozenset(REGISTRY.all_extensions()) | _UNREGISTERED_EXTS


def _is_placeholder(token: str) -> bool:
    segs = [s.lower() for s in token.strip("/").split("/") if s]
    if not segs:
        return True
    # Only the FIRST segment decides. Checking the first two swallowed real
    # paths wholesale --- ``app/user/models.py``, ``src/example/main.py`` ---
    # because ``user``, ``name``, ``org`` and ``example`` are ordinary
    # second-level directory names. The classic ``path/to/...`` shape is still
    # caught by its own first segment, and anything else that slips through
    # meets the anchoring rule in the resolver.
    return segs[0] in _PLACEHOLDER_SEGMENTS


def _path_shaped(token: str) -> bool:
    """True when a token asserts a repository path rather than being prose."""
    if not token or _URL_RE.match(token) or _TEMPLATED_RE.search(token):
        return False
    if token.startswith(("#", "~")) or token.startswith("//"):
        return False
    if _is_placeholder(token):
        return False
    base = token.rstrip("/").split("/")[-1]
    ext = base[base.rfind(".") :].lower() if "." in base else ""
    if "/" in token:
        # A slashed token needs a known extension or an explicit directory
        # shape, so prose like "and/or" never qualifies.
        return ext in KNOWN_EXTS or token.endswith("/")
    # A bare filename only counts with a known extension, so "e.g" and "i.e"
    # never qualify. The resolver calls it uncheckable regardless; keeping it
    # out here saves the work.
    return ext in KNOWN_EXTS and len(base) > len(ext) + 1


def _normalize(token: str) -> str:
    """Trim a reference to the path it names, without altering that path.

    ``lstrip("./")`` was wrong twice over: it strips *characters*, not a
    prefix, so ``.github/workflows/ci.yml`` became ``github/...`` (mis-keyed,
    then dropped as unanchored, and written to the database as something the
    document does not say), and ``../../docs/a.md`` lost the ``../`` segments
    the resolver needs to join the link against its own document.
    """
    t = token.strip().strip("`\"'")
    while t.startswith("./"):
        t = t[2:]
    t = t.lstrip("/")
    # Sentence punctuation that followed the reference in prose.
    #
    # The trailing slash goes with it, which keeps directory references out
    # of the detector. That is deliberate for this phase: Phase 1 measured
    # file references only, and admitting directories produced five findings
    # that were all illustration --- a document listing the conventional ADR
    # directory names (``docs/adr/``, ``docs/adrs/``, ``docs/decisions/``) is
    # naming a convention, not claiming this repository has one. Giving them
    # a measured class's confidence would be a claim the evidence does not
    # support.
    return t.rstrip(".,;:)").rstrip("/")


# ---------------------------------------------------------------------------
# Per-class extractors
#
# One function per class, all yielding DocReference, so a class can be retuned
# or dropped without touching the others.
# ---------------------------------------------------------------------------

_INLINE_CODE_RE = re.compile(r"(?<!`)`([^`\n]{2,120})`(?!`)")
_MD_LINK_RE = re.compile(r"\[[^\]\n]{0,200}?\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# Commands whose target is checkable against a manifest in the tree.
_COMMAND_RE = re.compile(
    r"\b(?:(make)\s+([A-Za-z0-9][\w./-]*)"
    r"|(npm|pnpm|yarn)\s+run\s+([A-Za-z0-9][\w:.-]*))"
)


_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


class _SectionTrail:
    """The heading trail above the line currently being read.

    Kept as a stack by heading level so an ancestor still counts: a reference
    under a plain ``#### Step 1`` inside ``## Adding a new language`` inherits
    the parent's instructional character, and a later ``## Architecture`` at
    the same level pops it again.
    """

    __slots__ = ("_stack",)

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def feed(self, line: str) -> bool:
        """Consume *line*; return True when it was a heading."""
        m = _HEADING_LINE_RE.match(line)
        if not m:
            return False
        level = len(m.group(1))
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, m.group(2).strip()))
        return True

    def trail(self) -> str:
        return " > ".join(title for _, title in self._stack)


def _from_links(line: str, lineno: int, doc: str) -> Iterator[DocReference]:
    """Markdown links: the LINK class, and the ANCHOR class from fragments."""
    for m in _MD_LINK_RE.finditer(line):
        raw = m.group(1)
        if _URL_RE.match(raw):
            continue
        target, _, frag = raw.partition("#")
        target = _normalize(target)
        if frag and not target:
            # Same-document anchor, checkable against this file's own headings.
            yield DocReference(
                DriftKind.ANCHOR, raw, f"{doc}#{frag}", doc, lineno, line.strip()
            )
            continue
        if not target or not _path_shaped(target):
            continue
        if frag:
            yield DocReference(
                DriftKind.ANCHOR, raw, f"{target}#{frag}", doc, lineno, line.strip()
            )
        yield DocReference(DriftKind.LINK, raw, target, doc, lineno, line.strip())


def _from_inline_code(line: str, lineno: int, doc: str) -> Iterator[DocReference]:
    """Backtick-quoted paths: the PATH class.

    Only paths. A backtick-quoted identifier is NOT emitted as a symbol
    reference; see :class:`~.models.DriftKind` for why that class does not
    exist.
    """
    for m in _INLINE_CODE_RE.finditer(line):
        raw = m.group(1).strip()
        norm = _normalize(raw)
        if _path_shaped(norm):
            yield DocReference(DriftKind.PATH, raw, norm, doc, lineno, line.strip())


def _from_commands(line: str, lineno: int, doc: str) -> Iterator[DocReference]:
    """Build-tool invocations: the COMMAND class.

    Read ONLY from inside inline code spans. Scanning bare prose matches
    English --- Phase 1's first run produced "make room", "make that", "make
    the" and "make no" as missing build targets, 43 of 55 findings and entirely
    noise. A command is a command when the author marked it as one.
    """
    for span in _INLINE_CODE_RE.finditer(line):
        for m in _COMMAND_RE.finditer(span.group(1)):
            target = f"make:{m.group(2)}" if m.group(1) else f"npm:{m.group(4)}"
            yield DocReference(
                DriftKind.COMMAND, m.group(0), target, doc, lineno, line.strip()
            )


_EXTRACTORS = (_from_links, _from_inline_code, _from_commands)


def extract(text: str, doc_path: str) -> list[DocReference]:
    """Every checkable assertion *text* makes, deduplicated, in document order."""
    seen: set[DocReference] = set()
    out: list[DocReference] = []
    trail = _SectionTrail()
    for lineno, line in prose_lines(text):
        # A heading updates the trail AND is still scanned: a section title
        # like "### 7.1 GitIndexer (`packages/core/ingestion/git_indexer.py`)"
        # asserts a path exactly as body prose does, and skipping headings
        # silently dropped one of this repository's real defects.
        trail.feed(line)
        section = trail.trail()
        for extractor in _EXTRACTORS:
            for ref in extractor(line, lineno, doc_path):
                ref = replace(ref, section=section)
                if ref in seen:
                    continue
                seen.add(ref)
                out.append(ref)
    return out




# ---------------------------------------------------------------------------
# Document selection
# ---------------------------------------------------------------------------


def is_checkable_document(rel_path: str) -> bool:
    """Whether *rel_path* is prose this repository wrote about itself.

    Excludes historical records by stem pattern (see
    :data:`~.constants.HISTORICAL_STEM_RE` for why a filename list is not
    enough), vendored trees, and anything the engine already classes as test
    or test support.

    Test documents are excluded through the shared
    :func:`~repowise.core.test_paths.is_test_related_path` rather than a local
    directory list, because a fixture is a fixture in more shapes than a list
    anticipates: fastapi stores deliberately-broken markdown under
    ``scripts/tests/.../data/`` to feed its own link fixer, and checking it
    reports the project's test inputs as its documentation.
    """
    p = Path(rel_path)
    if p.suffix.lower() not in MARKDOWN_SUFFIXES:
        return False
    if HISTORICAL_STEM_RE.match(p.stem):
        return False
    if set(p.parts) & VENDORED_DIR_PARTS:
        return False
    return not _in_test_tree(rel_path)


def _in_test_tree(rel_path: str) -> bool:
    """Whether *rel_path* sits inside a test or fixture directory.

    Asks the shared classifier about the containing DIRECTORY rather than the
    file, because ``is_test_related_path`` also recognises the ``test_*``
    filename convention --- and that is a convention of *code*, not prose.
    Applied to the whole path it classes ``docs/layers/TEST_INTELLIGENCE.md``,
    a document describing the test-intelligence layer, as a test file and
    silently drops one of this repository's real defects.

    Probing with a neutral filename keeps the directory half of the
    classifier, which is the half that matters here: it still recognises
    ``tests/fixtures/...`` and fastapi's
    ``scripts/tests/.../data/`` without guessing at either.
    """
    parent = rel_path.rsplit("/", 1)[0] if "/" in rel_path else ""
    probe = f"{parent}/_.md" if parent else "_.md"
    return is_test_related_path(probe)


def is_guide_document(rel_path: str) -> bool:
    """Whether *rel_path* teaches rather than describes.

    A path that resolves nowhere is likelier to be an invented illustration
    here, so the finding is stamped one confidence tier lower. This is a tier,
    not an exclusion: a tutorial naming a real file that moved is still drift.
    """
    # The STEM, not the whole path: searching the path meant a single
    # directory named ``examples/`` or ``templates/`` anywhere above a document
    # halved the confidence of every finding beneath it.
    return bool(GUIDE_STEM_RE.search(Path(rel_path).stem))
