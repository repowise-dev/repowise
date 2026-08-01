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
from dataclasses import dataclass
from pathlib import Path

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
    #: How many distinct documents name it. ``len(source_paths)``, named
    #: because it is the ranking signal rather than an incidental count.
    doc_frequency: int
    #: Whether the codebase has a symbol by this name. A term that is also a
    #: symbol can be rendered in backticks; a coined one cannot, because the
    #: grounding pass strips backticks off tokens it cannot resolve.
    is_indexed_symbol: bool


def _doc_paths(repo_root: Path) -> list[Path]:
    """The documents worth mining, in a fixed order. Never raises."""
    files: list[Path] = []
    try:
        for name in DOC_FILES:
            candidate = repo_root / name
            if candidate.is_file():
                files.append(candidate)
        for rel_dir in DOC_DIRS:
            directory = repo_root / rel_dir
            if not directory.is_dir():
                continue
            for candidate in sorted(directory.glob("*.md")):
                if candidate.is_file() and candidate not in files:
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
_BOLD_DEFINITION = re.compile(r"\*\*([A-Z][^*\n]{2,40})\*\*\s*[—–:-]\s*([^\n]{10,300})")


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
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if _HEADING_LINE.match(line):
            return None
        if stripped.startswith(_NOT_PROSE):
            continue
        sentence = _first_sentence(stripped)
        if _MIN_DEFINITION_CHARS <= len(sentence) <= _MAX_DEFINITION_CHARS:
            return sentence
        return None
    return None


@dataclass
class _Candidate:
    """A term under construction, accumulating across documents."""

    term: str
    definition: str | None = None
    definition_source: str | None = None
    source_paths: list[str] | None = None


def _harvest(repo_root: Path, *, limit: int | None = None) -> list[_Candidate]:
    """Every candidate term in document order, deduplicated, first spelling wins.

    ``limit`` stops the walk early, so a caller wanting the first N terms does
    not pay to read documents it will discard.
    """
    candidates: dict[str, _Candidate] = {}
    order: list[str] = []
    unreadable = 0

    for path in _doc_paths(repo_root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_DOC_BYTES]
        except OSError:
            unreadable += 1
            logger.warning("vocabulary: could not read %s; skipping it", path)
            continue
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:  # pragma: no cover — path came from repo_root
            rel = path.name

        # Definitions found for terms seen in *this* document, keyed the same
        # way as candidates so a repeat mention can fill a gap left earlier.
        definitions = {
            _normalise(m.group(1)).lower(): m.group(2).strip()
            for m in _BOLD_DEFINITION.finditer(text)
        }

        # Headings first, then bolded lead-ins — the order the planner's
        # binding was measured against. Which of the two a term came from
        # decides where its definition may be looked for: the sentence under a
        # heading belongs to that heading, but the lines under a bolded term
        # mid-paragraph belong to the paragraph, not to the term.
        matches = [(True, m) for m in _HEADING.finditer(text)]
        matches += [(False, m) for m in _BOLD_TERM.finditer(text)]
        for is_heading, match in matches:
            term = _normalise(match.group(1))
            if not _is_useful(term):
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
                definition = definitions.get(key)
                if not definition and is_heading:
                    definition = _definition_after_heading(text, match.end())
                if definition:
                    candidate.definition = definition
                    candidate.definition_source = rel

    if unreadable:
        logger.warning(
            "vocabulary: %d of the repository's documents could not be read",
            unreadable,
        )
    return [candidates[k] for k in order]


def extract_terms(repo_root: Path, *, max_terms: int = 200) -> list[str]:
    """Lift candidate subsystem names from the repository's own documentation.

    Returns terms in document order with duplicates removed. Never raises: a
    repository with no docs returns an empty list, which is a supported and
    common outcome, not a degraded one.
    """
    return [c.term for c in _harvest(repo_root, limit=max_terms)]


def extract_house_terms(
    repo_root: Path,
    *,
    max_terms: int = 200,
    known_symbols: frozenset[str] | set[str] | None = None,
) -> list[HouseTerm]:
    """The same terms as :func:`extract_terms`, with their sources and meanings.

    ``known_symbols`` are the names the codebase actually defines; a term is
    marked ``is_indexed_symbol`` when it matches one. Pass nothing and every
    term reports ``False``, which is the safe direction — an unbackticked term
    renders as plain prose, a wrongly-backticked one gets silently demoted.
    """
    symbols = {s.lower() for s in known_symbols} if known_symbols else set()
    terms = [
        HouseTerm(
            term=c.term,
            definition=c.definition,
            definition_source=c.definition_source,
            source_paths=tuple(c.source_paths or ()),
            doc_frequency=len(c.source_paths or ()),
            is_indexed_symbol=c.term.lower() in symbols,
        )
        for c in _harvest(repo_root, limit=max_terms)
    ]
    if not terms:
        # "We found nothing to read" must never be rendered as "this
        # repository has no vocabulary". Callers decide what to do with an
        # empty list; they cannot decide if they never learn it was empty.
        logger.warning(
            "vocabulary: no house terms found under %s — "
            "no readable %s and no top-level docs directory",
            repo_root,
            "/".join(DOC_FILES),
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
