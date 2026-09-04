"""The project's own words for its own subsystems, bound to real groups.

A wiki that reuses the repository's terminology reads as one document instead
of as a pile of pages. The naive way to get that is to hand the planner the
README, and it is measured to be a bad idea: nine thousand characters of
positioning prose alongside a structural task collapsed the outline from 23
pages covering 87.5% of the code to three pages covering 5.4%, and it invented
a docs path that matched the project's marketing language rather than its
directory tree.

So terminology enters through a keyhole. Terms are lifted from the repository's
docs here, and a term only survives if it can be **bound to a group that
structure already produced**. A feature that is marketed but not built has no
group to bind to and therefore gets no page. Structure leads and vocabulary
decorates, which is the only arrangement where a thin, stale or absent README —
the common case in the wild — costs nothing structural.

Extraction is deterministic rather than another model call. It has to run on
the keyless path, it has to give the same answer twice, and the job is small
enough that a model adds cost and variance without adding much.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repowise.core import fs_walk
from repowise.core.exclusion import build_exclude_spec, is_excluded

from .grouping import ConceptGroup

logger = logging.getLogger(__name__)

#: Documents worth mining, relative to the repository root. A doc that names a
#: subsystem in a heading is a much stronger signal than one that mentions it
#: in a sentence, so headings are what this reads.
#:
#: These are root-anchored on purpose. Glob patterns containing ``/`` match a
#: path *tail*, so a pattern like ``docs/*.md`` also matches
#: ``some/vendored/thing/docs/notes.md`` — which on this repository pulled
#: planning notes and marketing copy into the glossary and bound terms like
#: "Headline facts updated" to source directories.
DOC_FILES: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
)

#: Directories, relative to the root, whose top level is scanned for docs.
DOC_DIRS: tuple[str, ...] = ("docs", "doc", "adr", "docs/adr", "docs/decisions")

_MAX_DOC_BYTES = 200_000
_MAX_DOCS = 60

# A markdown or rst heading. The term is the heading text.
_HEADING = re.compile(r"^\s{0,3}#{1,4}\s+(.+?)\s*#*\s*$", re.MULTILINE)
# Bolded lead-ins are how glossaries are usually written: "**Blast radius** — ".
_BOLD_TERM = re.compile(r"\*\*([A-Z][^*\n]{2,40})\*\*")

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "for",
        "to",
        "in",
        "on",
        "with",
        "how",
        "what",
        "why",
        "when",
        "is",
        "are",
        "it",
        "this",
        "that",
        "you",
        "your",
        "we",
        "our",
        "using",
        "use",
        "usage",
        "getting",
        "started",
        "installation",
        "install",
        "quickstart",
        "quick",
        "start",
        "license",
        "contributing",
        "changelog",
        "table",
        "contents",
        "overview",
        "introduction",
        "intro",
        "example",
        "examples",
        "faq",
        "notes",
        "requirements",
        "setup",
        "configuration",
        "config",
        "options",
        "api",
        "reference",
        "guide",
        "tutorial",
        "docs",
        "documentation",
        "features",
        "roadmap",
        "credits",
        "acknowledgements",
        "badges",
        "support",
    }
)

_MIN_TERM_WORDS = 1
_MAX_TERM_WORDS = 4

# A heading that is a sentence is describing something, not naming it.
# "Local paths are masked" and "Headline facts updated 2026-06-22" are both
# real headings from this repository's own docs, and neither is a subsystem.
_SENTENCE_VERBS = frozenset(
    {
        "is",
        "are",
        "was",
        "were",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "can",
        "will",
        "would",
        "should",
        "must",
        "may",
        "updated",
        "added",
        "removed",
        "fixed",
        "works",
        "means",
        "needs",
        "gets",
        "goes",
        "runs",
        "returns",
        "uses",
        "supports",
        "requires",
        "shows",
        "makes",
        "keeps",
    }
)


def _normalise(term: str) -> str:
    term = re.sub(r"`([^`]*)`", r"\1", term)
    term = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", term)  # markdown links
    term = re.sub(r"[^\w\s/&+-]", " ", term)
    term = re.sub(r"\s+", " ", term).strip()
    return term


def _is_useful(term: str) -> bool:
    words = term.split()
    if not (_MIN_TERM_WORDS <= len(words) <= _MAX_TERM_WORDS):
        return False
    if all(w.lower() in _STOPWORDS for w in words):
        return False
    # A heading that is entirely boilerplate ("Getting Started") says nothing
    # about this repository in particular.
    meaningful = [w for w in words if w.lower() not in _STOPWORDS]
    if not meaningful:
        return False
    if not any(len(w) > 2 for w in meaningful):
        return False
    # Sentences and dated notes are prose, not names.
    if any(w.lower() in _SENTENCE_VERBS for w in words):
        return False
    if any(any(ch.isdigit() for ch in w) for w in words):
        return False
    # A name starts like a name. All-lowercase multi-word headings are almost
    # always prose fragments.
    return len(words) == 1 or words[0][:1].isupper()


@dataclass(frozen=True)
class HouseTerm:
    """A term the repository uses for itself, with what it was read from.

    ``definition`` is the repository's own sentence about the term — the line
    following the heading that names it, or the clause after a bolded lead-in.
    It is ``None`` far more often than not; a term with no sentence near it is
    still a term, and inventing one would be the whole failure this module
    exists to avoid.
    """

    term: str
    definition: str | None
    definition_source: str | None
    #: Repository-relative POSIX paths of every document that names the term,
    #: in the order they were read.
    source_paths: tuple[str, ...]
    #: How many distinct documents name it. This is the primary ranking
    #: signal: "how often do we write about this".
    doc_frequency: int
    #: How many source files use it in their own prose. This is the gate:
    #: "was it built". It is deliberately not added to ``doc_frequency`` —
    #: see :func:`extract_house_terms`.
    code_frequency: int
    #: Whether the codebase has a symbol by this name. A term that is also a
    #: symbol can be rendered in backticks; a coined one cannot, because the
    #: grounding pass strips backticks off tokens it cannot resolve.
    is_indexed_symbol: bool


def _rel(path: Path, repo_root: Path) -> str:
    """Repo-relative posix path, or the bare name when it is not under root."""
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:  # pragma: no cover — path came from repo_root
        return path.name


def _exclude_spec(repo_root: Path) -> Any:
    """The repository's own exclusion rules, or ``None``. Never raises.

    A malformed ``.gitignore`` line makes ``pathspec`` raise, and mining is a
    decoration: a repository with an unparseable ignore file should mine one
    document too many, not fail to generate.
    """
    try:
        return build_exclude_spec(repo_root)
    except Exception as exc:
        logger.warning("vocabulary: could not read exclusion rules (%s); mining unfiltered", exc)
        return None


def _is_minable(path: Path, repo_root: Path, spec: Any) -> bool:
    """Whether a document may be quoted as where a term is written.

    Two rules, both learned the same way. The repository's own exclusion rules
    are honoured because a path the user told git to ignore is scratch work,
    and the capability table cited ``local-stash/`` harnesses as authoritative
    definitions. Market-facing documents are dropped by name because their
    headings look exactly like subsystem names and nothing downstream can tell
    the difference.
    """
    return not is_excluded(_rel(path, repo_root), spec) and not _NON_NORMATIVE_DOC_NAMES.search(
        path.name
    )


def _doc_paths(repo_root: Path, *, patterns: tuple[str, ...] = ("*.md",)) -> list[Path]:
    """The documents worth mining, in a fixed order. Never raises."""
    files: list[Path] = []
    spec = _exclude_spec(repo_root)
    try:
        for name in DOC_FILES:
            candidate = repo_root / name
            if candidate.is_file() and _is_minable(candidate, repo_root, spec):
                files.append(candidate)
        for rel_dir in DOC_DIRS:
            directory = repo_root / rel_dir
            if not directory.is_dir():
                continue
            found: list[Path] = []
            for pattern in patterns:
                found.extend(directory.glob(pattern))
            for candidate in sorted(found):
                if (
                    candidate.is_file()
                    and candidate not in files
                    and _is_minable(candidate, repo_root, spec)
                ):
                    files.append(candidate)
                if len(files) >= _MAX_DOCS:
                    break
            if len(files) >= _MAX_DOCS:
                break
    except OSError:
        logger.warning(
            "vocabulary: could not list documents under %s; mining no terms for this repository",
            repo_root,
        )
        return []
    return files


# ---------------------------------------------------------------------------
# reStructuredText
# ---------------------------------------------------------------------------
#
# A large share of Python projects document in reStructuredText, and every
# pattern above is markdown-only. Read as markdown, a reST document yields no
# headings at all — not an error, just silence, which is the worst shape a
# failure can take here. Of seven repositories checked, five were in this
# position: flask, requests, django, sphinx and reflex.

#: The punctuation reST uses to underline a title. Any of these, repeated.
_RST_UNDERLINE = re.compile(r"^([=\-`:.'\"~^_*+#<>])\1{2,}\s*$")
#: ``.. note::``, ``.. _label:``, ``.. image::`` — markup, never a title.
_RST_DIRECTIVE = re.compile(r"^\s*\.\.\s")

#: Extensions a reStructuredText document is written under. ``.txt`` is here
#: because Sphinx's ``source_suffix`` is a project setting and a large project
#: is as likely to have set it to ``.txt`` as to have left it at ``.rst`` —
#: django writes its entire ``docs/`` tree that way. Read as markdown a
#: ``.txt`` document yields nothing at all, which is indistinguishable from a
#: repository that documents nothing.
#:
#: The cost of guessing wrong is bounded: a ``.txt`` that is not reST has no
#: title underlines, so the reST scan returns no sections rather than bad ones.
_RST_SUFFIXES = frozenset({".rst", ".txt"})
#: A line that is reStructuredText markup rather than a sentence: bullet and
#: enumerated list items, grid- and simple-table rules and rows, line blocks,
#: and field lists or bare roles.
#:
#: The markdown scan has always refused these shapes — see ``_NOT_PROSE`` — and
#: the reST scan reading them as prose is how a table of contents supplied a
#: subsystem's definition. Mined from django, whose "Models" section opens with
#: a grid of ``:doc:`` links, the sentence taken was
#: ``:doc:`Introduction to models <topics/db/models>` |`` — markup, a wrong
#: definition, and a stray pipe that would break any table it is rendered into.
_RST_MARKUP_LINE = re.compile(r"^\s*(?:[*+\-|>:=~^#]|\d+[.)]\s)")


def _is_rst_underline(line: str, title: str) -> bool:
    """Whether ``line`` underlines ``title``.

    reStructuredText requires the underline to be at least as long as the
    title it carries. Checking that is what separates a real section title
    from a line of prose that happens to sit above a horizontal rule or a
    row of dashes in a table.
    """
    if not _RST_UNDERLINE.match(line):
        return False
    return len(line.rstrip()) >= len(title.rstrip())


def _rst_sections(lines: list[str]) -> list[tuple[str, int]]:
    """``(title, index of its underline)`` for each section title.

    The overline form — punctuation above *and* below the title — falls out
    for free: the overline is not a title (the line under it is not an
    underline of it), and the title/underline pair below matches normally.
    """
    out: list[tuple[str, int]] = []
    for i in range(len(lines) - 1):
        title = lines[i].strip()
        if not title or _RST_DIRECTIVE.match(lines[i]) or _RST_UNDERLINE.match(title):
            continue
        if _is_rst_underline(lines[i + 1], title):
            out.append((title, i + 1))
    return out


def _definition_after_rst_section(lines: list[str], underline: int) -> str | None:
    """The first prose sentence of a reST section.

    Stops at the next title for the same reason the markdown scan does: a
    sentence lifted from the following section is a confident wrong
    definition, which is worse than none.
    """
    directive_indent: int | None = None
    stop = min(underline + 1 + _DEFINITION_SCAN_LINES, len(lines))
    for offset in range(underline + 1, stop):
        line = lines[offset]
        stripped = line.strip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if directive_indent is not None:
            # Still inside the directive: its body is indented under it.
            if indent > directive_indent:
                continue
            directive_indent = None
        if _RST_DIRECTIVE.match(line):
            # A directive and everything it contains is markup, not prose.
            # ``.. note::`` happens to hold a sentence; ``.. code-block::``
            # holds code and ``.. toctree::`` holds filenames, and a
            # definition scan cannot tell them apart from the outside.
            directive_indent = indent
            continue
        if _RST_UNDERLINE.match(stripped):
            continue
        # A line that is itself underlined is the next section's title.
        if offset + 1 < len(lines) and _is_rst_underline(lines[offset + 1], stripped):
            return None
        if _RST_MARKUP_LINE.match(stripped):
            # Markup, not a sentence. Skipped rather than taken, exactly as the
            # markdown scan skips a table row or a badge line above the prose.
            continue
        paragraph = _join_wrapped(lines, offset, stop)
        sentence = _first_sentence(_strip_rst_roles(paragraph))
        if _MIN_DEFINITION_CHARS <= len(sentence) <= _MAX_DEFINITION_CHARS:
            return sentence
        return None
    return None


def _join_wrapped(lines: list[str], start: int, stop: int) -> str:
    """The paragraph beginning at ``start``, rejoined into one string.

    reStructuredText hard-wraps prose at the column, so a line is a line and
    not a sentence. Reading one line was how django's Forms section defined
    itself as "Django provides a rich framework to facilitate the creation of
    forms and the" — the author's sentence, cut where the editor wrapped it.

    Stops at the paragraph break, at markup, and at the next section title,
    which are the same boundaries the caller respects; the caller's scan
    window bounds it.
    """
    parts = [lines[start].strip()]
    for offset in range(start + 1, stop):
        if _SENTENCE_END.search(" ".join(parts)):
            break
        nxt = lines[offset].strip()
        if not nxt or _RST_MARKUP_LINE.match(nxt) or _RST_DIRECTIVE.match(lines[offset]):
            break
        if offset + 1 < len(lines) and _is_rst_underline(lines[offset + 1], nxt):
            break
        parts.append(nxt)
    return " ".join(parts)


#: ``:doc:`quickstart``` and ``:class:`Flask``` are cross-references. The text
#: inside is what a reader sees, so that is what a definition should carry.
_RST_ROLE = re.compile(r":[a-zA-Z:+-]+:`([^`]*)`")
_RST_LINK = re.compile(r"`([^`<]*?)\s*(?:<[^>]*>)?`_+")


def _strip_rst_roles(text: str) -> str:
    text = _RST_ROLE.sub(r"\1", text)
    text = _RST_LINK.sub(r"\1", text)
    return re.sub(r"``([^`]*)``", r"\1", text)


# A line that opens a new section. The definition scan stops here: running on
# into the next section hands back a sentence about a different subject, which
# reads as authoritative and is wrong.
_HEADING_LINE = re.compile(r"^\s{0,3}#{1,4}\s+\S")
# Lines that are structure rather than prose — tables, fences, lists, quotes.
_NOT_PROSE = ("|", "```", "-", "*", ">", "!")
# Shorter than this is a lead-in ("See below.", "Two parts:"), not a meaning.
_MIN_DEFINITION_CHARS = 20
_MAX_DEFINITION_CHARS = 300
# How far past a heading to look for the sentence that explains it.
_DEFINITION_SCAN_LINES = 5

_SENTENCE_END = re.compile(r"(?<=[.!?])(?:\s|$)")

# A bolded lead-in that carries its own definition: "**Blast radius** — ...".
#
# The gap around the separator is horizontal whitespace, not ``\s``: a lead-in
# and the sentence that defines it are on one line, and that is the whole shape
# this pattern is for. Letting the gap cross a newline turned a bolded label
# standing alone on its line into a claim on whatever the next line held. On
# django's contents page, "**Models:**" followed by a row of links produced
#
#     Models -> "doc:`Introduction to models <topics/db/models>` |"
#
# where the separator matched the colon of ``:doc:`` — markup, a wrong
# definition, and a stray pipe that breaks any table it is rendered into.
_BOLD_DEFINITION = re.compile(r"\*\*([A-Z][^*\n]{2,40})\*\*[ \t]*[—–:-][ \t]*([^\n]{10,300})")


def _first_sentence(line: str) -> str:
    match = _SENTENCE_END.search(line)
    return (line[: match.start() + 1] if match else line).strip()


def _definition_after_heading(text: str, offset: int) -> str | None:
    """The first prose sentence between a heading and the next one.

    ``offset`` is the *end* of the heading text, not its start. ``_HEADING``
    opens with ``\\s{0,3}``, which in multiline mode lets a match begin on the
    blank line above; anchoring on the end sidesteps that entirely.
    """
    lines = text[offset:].split("\n")[1 : _DEFINITION_SCAN_LINES + 1]
    for start, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _HEADING_LINE.match(line):
            return None
        if stripped.startswith(_NOT_PROSE):
            continue
        sentence = _first_sentence(_join_markdown_wrapped(lines, start))
        if _MIN_DEFINITION_CHARS <= len(sentence) <= _MAX_DEFINITION_CHARS:
            return sentence
        return None
    return None


def _join_markdown_wrapped(lines: list[str], start: int) -> str:
    """The markdown paragraph beginning at ``start``, rejoined into one string.

    The reST scan has done this since #1248; the markdown scan never did, and a
    hard-wrapped README is as common as a hard-wrapped ``.txt``. Reading one
    line returned the author's sentence cut where their editor wrapped it —
    "Blast radius is the set of accounts a posting can reach through the
    ledger" — which then reads as a definition that trails off mid-thought.

    Stops at the paragraph break, at the next heading, at anything that is not
    prose, and as soon as the text has a sentence in it.
    """
    parts = [lines[start].strip()]
    for offset in range(start + 1, len(lines)):
        if _SENTENCE_END.search(" ".join(parts)):
            break
        nxt = lines[offset].strip()
        if not nxt or nxt.startswith(_NOT_PROSE) or _HEADING_LINE.match(lines[offset]):
            break
        parts.append(nxt)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Which documents to read
# ---------------------------------------------------------------------------

#: Files whose headings are version numbers and shipped-feature blurbs rather
#: than subsystem names. A changelog is usually the largest document in a
#: repository and the least useful one to mine: on this repository it consumed
#: 142 of the first 200 term slots before the tool guide was ever opened.
_RELEASE_NOTE_NAMES = re.compile(r"(change ?log|releases?|news|history)\.(md|rst|txt)$", re.I)
#: Documents about the market rather than about the system. Their headings are
#: product claims, competitor names and score tables, so mining them cites a
#: positioning document as "where this capability is written". Dropped by name
#: because their headings are indistinguishable from a real subsystem's.
_NON_NORMATIVE_DOC_NAMES = re.compile(
    r"(benchmarks?|competitive[_ -]?analysis|competitors?|pricing|roadmap)\.(md|rst|txt)$", re.I
)
_VERSION_HEADING = re.compile(r"^v?\d+\.\d+")
#: A document has to be overwhelmingly version headings before it is dropped. A
#: guide that cites a few release numbers is not release notes, and dropping a
#: large guide by accident loses more vocabulary than a changelog ever adds.
_RELEASE_NOTE_HEADING_RATIO = 0.6
_MIN_HEADINGS_TO_JUDGE = 8


def _is_release_notes(path: Path, text: str) -> bool:
    if _RELEASE_NOTE_NAMES.search(path.name):
        return True
    headings = _HEADING.findall(text)
    if len(headings) < _MIN_HEADINGS_TO_JUDGE:
        return False
    versiony = sum(1 for h in headings if _VERSION_HEADING.match(h.strip()))
    return versiony / len(headings) > _RELEASE_NOTE_HEADING_RATIO


# ---------------------------------------------------------------------------
# Which headings name something
# ---------------------------------------------------------------------------

#: A pronoun makes a heading a sentence about the reader or about us, never a
#: name: "Your agent stops guessing", "See all of it", "Who it's for". This is
#: grammar rather than a list of words we happened to dislike, so it carries
#: to a repository whose marketing copy reads nothing like ours.
_PRONOUN = re.compile(
    r"\b(you|your|yours|i|me|my|we|us|our|ours|it|its|they|them|their|theirs|he|she|him|her|his|hers)\b",
    re.I,
)

#: Numbering a heading carries ("2 · Distill: command-output compression",
#: "3. Ingestion") is document structure, not part of the name.
_ENUMERATOR = re.compile(r"^\s*\d+[.)]?\s*[·•\-–—]?\s+")
#: A heading of the form "Name: what it does" names the thing before the colon
#: and explains it after. Both halves are worth offering as candidates; the
#: lead is usually the term.
_COLON_SPLIT = re.compile(r"^([^:]{2,40}):\s+(\S.*)$")


def _is_name_like(term: str, *, excluded: frozenset[str]) -> bool:
    """Whether a heading names a thing, as opposed to claiming something.

    Applied on top of :func:`_is_useful`, which already rejects the obvious
    sentence shapes. This adds the two tests that matter for a term list that
    gets rendered: no pronouns, and not the repository's own name — which
    leads every frequency count in every repository and teaches nobody
    anything.
    """
    if term.lower() in excluded:
        return False
    return not _PRONOUN.search(term)


def _expand_heading(raw: str) -> list[str]:
    """The spellings of a heading worth considering as a name.

    A numbered heading yields its text without the number; a "Name: gloss"
    heading yields the name. Both are offered alongside the original, and
    :func:`_is_useful` decides which survive — a heading carrying a digit is
    rejected outright, so without stripping the enumerator a numbered section
    contributes nothing at all.
    """
    spellings = [raw]
    stripped = _ENUMERATOR.sub("", raw).strip()
    if stripped and stripped != raw:
        spellings.append(stripped)
    for candidate in list(spellings):
        colon = _COLON_SPLIT.match(candidate)
        if colon:
            lead = colon.group(1).strip()
            if lead and lead not in spellings:
                spellings.append(lead)
    return spellings


def _repo_own_names(repo_root: Path) -> frozenset[str]:
    """What this repository calls itself, read from the repository."""
    names = {repo_root.name.lower()}
    for name in list(names):
        names.add(name.replace("-", " "))
        names.add(name.replace("_", " "))
        names.add(name.replace("-", ""))
    return frozenset(n for n in names if n)


@dataclass
class _Candidate:
    """A term under construction, accumulating across documents."""

    term: str
    definition: str | None = None
    definition_source: str | None = None
    source_paths: list[str] | None = None


def _harvest(
    repo_root: Path,
    *,
    limit: int | None = None,
    expand: Callable[[str], list[str]] | None = None,
    accept: Callable[[str], bool] | None = None,
    skip_release_notes: bool = False,
    read_rst: bool = False,
) -> list[_Candidate]:
    """Every candidate term in document order, deduplicated, first spelling wins.

    ``limit`` stops the walk early, so a caller wanting the first N terms does
    not pay to read documents it will discard.

    The three hooks are how the ranked view asks for more than the planner
    does without moving the planner's input. Called with none of them this is
    the harvest ``extract_terms`` has always performed, term for term.

    ``expand`` turns one raw heading into several candidate spellings.
    ``accept`` is an extra test applied after :func:`_is_useful`.
    ``skip_release_notes`` drops documents whose headings are version numbers.
    ``read_rst`` reads reStructuredText documents as reStructuredText rather
    than looking for markdown headings in them and finding none.
    """
    candidates: dict[str, _Candidate] = {}
    order: list[str] = []
    unreadable = 0
    skipped: list[str] = []
    sectionless: list[str] = []
    rst_sections_seen = 0
    rst_sections_undefined = 0

    patterns = ("*.md", "*.rst", "*.txt") if read_rst else ("*.md",)
    for path in _doc_paths(repo_root, patterns=patterns):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_DOC_BYTES]
        except OSError:
            unreadable += 1
            logger.warning("vocabulary: could not read %s; skipping it", path)
            continue
        if skip_release_notes and _is_release_notes(path, text):
            skipped.append(path.name)
            continue
        rel = _rel(path, repo_root)

        # Definitions found for terms seen in *this* document, keyed the same
        # way as candidates so a repeat mention can fill a gap left earlier.
        definitions = {
            _normalise(m.group(1)).lower(): m.group(2).strip()
            for m in _BOLD_DEFINITION.finditer(text)
        }

        # Headings first, then bolded lead-ins — the order the planner's
        # binding was measured against. Each entry carries the definition
        # already resolved, because where a definition may be looked for
        # depends on where the term came from: the sentence under a heading
        # belongs to that heading, but the lines under a bolded term
        # mid-paragraph belong to the paragraph, not to the term.
        entries: list[tuple[str, str | None]] = []
        if read_rst and path.suffix.lower() in _RST_SUFFIXES:
            lines = text.split("\n")
            sections = _rst_sections(lines)
            if not sections:
                # A document read under a markup guess that turned out wrong
                # is indistinguishable downstream from a document with nothing
                # in it. Counted, so "we found no vocabulary" can be told
                # apart from "we read the wrong thing".
                sectionless.append(rel)
            entries = [
                (title, _definition_after_rst_section(lines, underline))
                for title, underline in sections
            ]
            rst_sections_seen += len(entries)
            rst_sections_undefined += sum(1 for _t, d in entries if d is None)
        else:
            entries = [
                (m.group(1), _definition_after_heading(text, m.end()))
                for m in _HEADING.finditer(text)
            ]
        # Bolded lead-ins read the same in both markup languages.
        entries += [(m.group(1), None) for m in _BOLD_TERM.finditer(text)]

        for raw, heading_definition in entries:
            # Expansion runs on the raw heading, before normalisation:
            # normalising strips the punctuation — the colon, the bullet —
            # that says where the name ends and the gloss begins.
            spellings = expand(raw) if expand is not None else [raw]
            for spelling in spellings:
                term = _normalise(spelling)
                if not _is_useful(term):
                    continue
                if accept is not None and not accept(term):
                    continue
                key = term.lower()
                candidate = candidates.get(key)
                if candidate is None:
                    if limit is not None and len(order) >= limit:
                        return [candidates[k] for k in order]
                    candidate = _Candidate(term=term, source_paths=[])
                    candidates[key] = candidate
                    order.append(key)
                assert candidate.source_paths is not None
                if rel not in candidate.source_paths:
                    candidate.source_paths.append(rel)
                if candidate.definition is None:
                    definition = definitions.get(key) or heading_definition
                    if definition:
                        candidate.definition = definition
                        candidate.definition_source = rel

    if unreadable:
        logger.warning(
            "vocabulary: %d of the repository's documents could not be read",
            unreadable,
        )
    if skipped:
        logger.info(
            "vocabulary: skipped %d release-note document(s): %s",
            len(skipped),
            ", ".join(sorted(skipped)),
        )
    if sectionless:
        logger.info(
            "vocabulary: %d document(s) read as reStructuredText carried no section "
            "titles and contributed no terms: %s",
            len(sectionless),
            ", ".join(sorted(sectionless)[:10]),
        )
    if rst_sections_seen:
        # A term with no sentence is a supported outcome, but a document whose
        # every section is markup is usually a table of contents rather than
        # prose — worth being able to see rather than inferring from an
        # unexplained absence of definitions.
        logger.info(
            "vocabulary: %d of %d reStructuredText sections carried no defining sentence",
            rst_sections_undefined,
            rst_sections_seen,
        )
    return [candidates[k] for k in order]


# ---------------------------------------------------------------------------
# What the code says about itself
# ---------------------------------------------------------------------------

#: Directories that hold somebody else's code on top of what the shared
#: walker already prunes. Their prose is about their subsystems, not ours.
_EXTRA_PRUNED_DIRS = fs_walk.PRUNED_DIRS_DERIVED | frozenset(
    {"vendor", "third_party", "thirdparty", "site-packages"}
)

#: Python docstrings, and the block comments JSDoc and TSDoc are written in.
#: Both are prose a maintainer wrote about a unit of code, which is what makes
#: them the right place to ask whether a term names something that was built.
#: Reading only Python here would score every TypeScript-first repository zero
#: and reject its entire vocabulary.
_SOURCE_PROSE = {
    ".py": re.compile(r'(?:^|\n)\s*(?:[rubfRUBF]{0,2})"""(.*?)"""', re.S),
    ".js": re.compile(r"/\*\*(.*?)\*/", re.S),
}
for _ext in (".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"):
    _SOURCE_PROSE[_ext] = _SOURCE_PROSE[".js"]

_MAX_SOURCE_FILES = 6_000
_MAX_SOURCE_BYTES = 60_000
_MAX_PROSE_BLOCKS = 8
#: Leading ``*`` on every line of a JSDoc block is layout, not text.
_JSDOC_GUTTER = re.compile(r"^\s*\*[ \t]?", re.M)


def _scan_tree(repo_root: Path) -> tuple[list[tuple[str, str]], frozenset[str]]:
    """One pruned pass over the repository, answering both tree questions.

    Returns ``(prose, directory_names)`` — ``(path, prose)`` for every source
    file that documents itself, and every name the repository has given a
    directory. Both come from the same walk because both need the whole tree
    and the walk is the expensive part.

    Never raises: an unreadable file is one fewer signal, not a failed
    generation.
    """
    prose: list[tuple[str, str]] = []
    dir_names: set[str] = set()
    unreadable = 0
    truncated = False
    spec = _exclude_spec(repo_root)

    for dirpath, dirnames, filenames in fs_walk.walk_repo(repo_root, prune_dirs=_EXTRA_PRUNED_DIRS):
        # Hidden directories are tool and agent territory — CI definitions,
        # editor state, hook configuration — never the subsystem prose that
        # answers "was this term built". The walker already prunes the named
        # ones and any nested checkout, but a *stale* worktree copy has had its
        # ``.git`` file removed and reads as ordinary source: on this
        # repository that put a second, deeper copy of every docstring under
        # ``.claude/`` ahead of the original, and the mined definitions cited
        # paths a reader has no reason to open. Pruning the whole class costs
        # one string test and no repository keeps its documented source here.
        # A tree the repository excludes from its own index is scratch work,
        # not the repository talking about itself. Skipped here rather than
        # filtered later so a large stash costs no walk: the capability table
        # was citing ``local-stash/`` harnesses as where a capability is
        # written, and those files are absent from every other surface.
        dirnames[:] = [
            d
            for d in dirnames
            if not d.startswith(".") and not is_excluded(_rel(dirpath / d, repo_root) + "/", spec)
        ]
        for name in dirnames:
            dir_names.add(_singular(name.lower()))
        if truncated:
            continue
        for name in sorted(filenames):
            pattern = _SOURCE_PROSE.get(Path(name).suffix)
            if pattern is None:
                continue
            if len(prose) >= _MAX_SOURCE_FILES:
                # A floor rather than a count. Said out loud, because a
                # silently truncated corpus reads downstream as "the code
                # does not use this term" — which is the gate's reject.
                logger.info(
                    "vocabulary: stopped reading source prose at %d files; "
                    "term code frequencies are a floor, not a count",
                    _MAX_SOURCE_FILES,
                )
                truncated = True
                break
            path = dirpath / name
            rel = _rel(path, repo_root)
            if is_excluded(rel, spec):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_SOURCE_BYTES]
            except OSError:
                unreadable += 1
                continue
            blocks = pattern.findall(text)[:_MAX_PROSE_BLOCKS]
            if not blocks:
                continue
            joined = "\n".join(_JSDOC_GUTTER.sub("", b) for b in blocks)
            prose.append((rel, joined))

    if unreadable:
        logger.warning("vocabulary: %d source files could not be read for prose", unreadable)
    return prose, frozenset(dir_names)


#: The gaps between a term's words. Prose writes "co-change", an identifier
#: writes "co_change" and a heading writes "Co change"; they are one term.
_WORD_GAP = re.compile(r"[\s_\-]+")


def term_words(term: str) -> list[str]:
    return [w for w in _WORD_GAP.split(term) if w]


def phrase_pattern(term: str) -> re.Pattern[str]:
    """Match a term however the code spells the gaps between its words.

    ``bug magnet`` has to find ``bug magnet``, ``bug-magnet`` and
    ``bug_magnet``, because the docs write it one way and the code the other.
    """
    words = [re.escape(w) for w in term_words(term)]
    return re.compile(r"\b" + r"[\s_\-]+".join(words) + r"\b", re.I)


def _definition_from_prose(prose: str, pattern: re.Pattern[str]) -> str | None:
    """A sentence in code prose that opens by naming the term.

    A sentence leading with the term is usually the best definition in the
    repository — better than a heading's follow-on line, which is often a
    lead-in rather than a meaning. A sentence that merely mentions it is not a
    definition and is not taken.
    """
    for raw in re.split(r"(?<=[.!?])\s+", prose):
        sentence = " ".join(raw.split())
        if not (_MIN_DEFINITION_CHARS <= len(sentence) <= _MAX_DEFINITION_CHARS):
            continue
        if pattern.match(sentence):
            return sentence
    return None


def extract_terms(repo_root: Path, *, max_terms: int = 200) -> list[str]:
    """Lift candidate subsystem names from the repository's own documentation.

    Returns terms in document order with duplicates removed. Never raises: a
    repository with no docs returns an empty list, which is a supported and
    common outcome, not a degraded one.
    """
    return [c.term for c in _harvest(repo_root, limit=max_terms)]


#: How many source paths to keep on a term. A term used by 300 files does not
#: need 300 citations; the count is kept in full on ``code_frequency``.
_MAX_CODE_PATHS = 40


def _warn_empty(repo_root: Path) -> None:
    """Nothing was read at all — as opposed to read and rejected."""
    logger.warning(
        "vocabulary: no house terms found under %s — no readable %s and no "
        "top-level docs directory",
        repo_root,
        "/".join(DOC_FILES),
    )


def _survives_single_word_test(term: str, dir_names: frozenset[str]) -> bool:
    """Whether a one-word term is a name rather than an ordinary word.

    A single common English word is never a house term however often it
    appears — "Code", "Response", "Query" and "Bare" all rank near the top on
    raw frequency and none of them names a subsystem. A single word earns its
    place when its *shape* marks it as a name (an acronym like MCP or FTS, an
    internal capital like PageRank), or when the codebase has named a whole
    directory after it.

    The directory test is deliberately a match against the **whole** directory
    name rather than against the words inside one. Matching word-by-word looks
    stricter than it is: a repository of a few thousand files spells almost
    every common English word somewhere inside some path, so the test passes
    everything and stops nothing. "dead_code" must not be what lets "Code"
    through.
    """
    words = term_words(term)
    if len(words) != 1:
        return True
    word = words[0]
    acronym = word.isupper() and 2 <= len(word) <= 5
    internal_capital = any(c.isupper() for c in word[1:])
    return acronym or internal_capital or _singular(word.lower()) in dir_names


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def extract_house_terms(
    repo_root: Path,
    *,
    max_terms: int = 200,
    known_symbols: frozenset[str] | set[str] | None = None,
) -> list[HouseTerm]:
    """The repository's own words for its own subsystems, ranked and gated.

    The same harvest :func:`extract_terms` performs, plus three things it does
    not need and a rendered term list cannot do without.

    **Release notes are skipped.** Their headings are version numbers, and a
    changelog is usually the largest document in the repository.

    **Two frequency signals, kept apart on purpose.** ``doc_frequency`` counts
    the documents that name the term — "is this something we write about",
    and the ranking key. ``code_frequency`` counts the source files whose own
    prose uses it — "was it built", and the gate. Adding them together lets a
    common English word appearing in 180 docstrings outrank the name of an
    actual subsystem; ranking on one and gating on the other does not. The
    gate is the same bind-or-drop discipline the planner already applies: a
    marketed term with nothing behind it does not survive.

    **A term must look like a name.** No pronouns, no repository name, and a
    single word must be shaped like one or be spelled in a directory.

    ``known_symbols`` are the names the codebase defines; a term is marked
    ``is_indexed_symbol`` when it matches one. Pass nothing and every term
    reports ``False``, which is the safe direction — an unbackticked term
    renders as plain prose, a wrongly-backticked one is silently demoted.
    """
    symbols = {s.lower() for s in known_symbols} if known_symbols else set()
    excluded = _repo_own_names(repo_root)

    candidates = _harvest(
        repo_root,
        limit=max_terms,
        expand=_expand_heading,
        accept=lambda t: _is_name_like(t, excluded=excluded),
        skip_release_notes=True,
        read_rst=True,
    )
    if not candidates:
        _warn_empty(repo_root)
        return []

    patterns = {c.term.lower(): phrase_pattern(c.term) for c in candidates}
    # Every match of a term's pattern contains that term's first word, so a
    # substring test on the lowercased prose rejects most (file, term) pairs
    # before the regex engine is started. It is a necessary condition and
    # nothing else, so the terms that survive are exactly the ones that
    # survived before — this is the same scan, done on a fraction of the
    # pairs. It is worth the two lines: the corpus is every source file in the
    # repository and the pattern set is up to two hundred, so the product is
    # where the whole cost of mining sits.
    first_words = {c.term.lower(): term_words(c.term)[0].lower() for c in candidates}
    code_files: dict[str, list[str]] = {c.term.lower(): [] for c in candidates}
    code_definitions: dict[str, tuple[str, str]] = {}
    prose_corpus, dir_names = _scan_tree(repo_root)
    for rel, prose in prose_corpus:
        folded = prose.lower()
        for key, pattern in patterns.items():
            if first_words[key] not in folded:
                continue
            if not pattern.search(prose):
                continue
            code_files[key].append(rel)
            if key not in code_definitions:
                sentence = _definition_from_prose(prose, pattern)
                if sentence:
                    code_definitions[key] = (sentence, rel)

    terms: list[HouseTerm] = []
    dropped_unbuilt = 0
    dropped_common_word = 0
    for candidate in candidates:
        key = candidate.term.lower()
        hits = code_files[key]
        if not hits:
            dropped_unbuilt += 1
            continue
        if not _survives_single_word_test(candidate.term, dir_names):
            dropped_common_word += 1
            continue
        definition = candidate.definition
        definition_source = candidate.definition_source
        if definition is None and key in code_definitions:
            definition, definition_source = code_definitions[key]
        doc_paths = tuple(candidate.source_paths or ())
        terms.append(
            HouseTerm(
                term=candidate.term,
                definition=definition,
                definition_source=definition_source,
                source_paths=doc_paths + tuple(hits[:_MAX_CODE_PATHS]),
                doc_frequency=len(doc_paths),
                code_frequency=len(hits),
                is_indexed_symbol=key in symbols,
            )
        )

    # How many documents name it, then multi-word terms ahead of single-word
    # ones, then how much of the code uses it.
    #
    # The middle key is the one doing the work. Document frequency is the
    # right primary signal and it discriminates poorly in practice: most
    # repositories have two or three documents worth mining, so nearly every
    # term ties at one and the sort falls through. Word count breaks that tie
    # in the right direction, because a subsystem is almost always named with
    # two words ("blast radius", "dead code", "change risk") and an ordinary
    # English word is almost always one.
    terms.sort(
        key=lambda t: (
            -t.doc_frequency,
            len(term_words(t.term)) == 1,
            -t.code_frequency,
            t.term.lower(),
        )
    )
    logger.info(
        "vocabulary: %d house terms from %d candidates "
        "(%d named but not built, %d ordinary words), %d source files read",
        len(terms),
        len(candidates),
        dropped_unbuilt,
        dropped_common_word,
        len(prose_corpus),
    )
    if not terms:
        # Distinct from having nothing to read: the documents were read and
        # every term in them failed the gate. Usually that means the source
        # prose was not found — a language this cannot read, or a layout where
        # the code sits outside the walked tree.
        logger.warning(
            "vocabulary: no house terms found under %s — %d candidates were "
            "read from the documents and none survived; %d source files "
            "contributed prose",
            repo_root,
            len(candidates),
            len(prose_corpus),
        )
    return terms


# ---------------------------------------------------------------------------
# Binding
# ---------------------------------------------------------------------------


def _tokens(text: str) -> set[str]:
    parts = re.split(r"[^A-Za-z0-9]+", text)
    out: set[str] = set()
    for part in parts:
        if not part:
            continue
        for piece in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*|[a-z]+|\d+", part) or [part]:
            low = piece.lower()
            if len(low) > 2 and low not in _STOPWORDS:
                out.add(low)
                # A trailing plural should still match its directory.
                if low.endswith("s"):
                    out.add(low[:-1])
    return out


def _group_tokens(group: ConceptGroup) -> tuple[set[str], set[str]]:
    """The group's words, split into structural ones and incidental ones.

    Directory names are what the codebase calls a *place*; filenames are what
    it calls the things in that place. A term matching only a filename is
    usually a coincidence — every group contains a file with "client" or
    "store" in its name — so the two are scored differently.
    """
    structural = _tokens(group.target_path)
    for d in group.dirs:
        structural |= _tokens(d)
    incidental: set[str] = set()
    for member in group.members:
        incidental |= _tokens(member.rsplit("/", 1)[-1].rsplit(".", 1)[0])
    return structural, incidental - structural


def bind_terms(
    terms: list[str],
    groups: dict[str, ConceptGroup],
) -> dict[str, str]:
    """Bind each term to at most one group, and each group to at most one term.

    A term binds only when the codebase spells **all** of it and at least part
    of it in a directory name. Both halves matter. Requiring every word keeps
    "Change Risk" off a group that merely contains the word "change";
    requiring a directory hit keeps a term from attaching to whichever group
    happens to hold a file with a matching name. A marketed feature with no
    code cluster satisfies neither and therefore never becomes a page title,
    which is the entire point of binding rather than injecting.

    Returns ``{group_id: term}``. Ties break on how much of the match was
    structural, then the term's document order, then the group id, so two runs
    over an unchanged repository bind identically.
    """
    token_cache = {gid: _group_tokens(g) for gid, g in groups.items()}
    scored: list[tuple[int, int, int, str, str]] = []
    for order, term in enumerate(terms):
        wanted = _tokens(term)
        if not wanted:
            continue
        for gid, (structural, incidental) in token_cache.items():
            in_struct = wanted & structural
            if not in_struct:
                continue
            if wanted - structural - incidental:
                # Some word of the term is absent from this group entirely.
                continue
            scored.append((-len(in_struct), -len(wanted), order, gid, term))

    scored.sort()
    bound: dict[str, str] = {}
    used_terms: set[str] = set()
    for _s, _w, _order, gid, term in scored:
        if gid in bound or term.lower() in used_terms:
            continue
        bound[gid] = term
        used_terms.add(term.lower())
    return bound
