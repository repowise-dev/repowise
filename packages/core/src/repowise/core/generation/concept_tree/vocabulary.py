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

import re
from pathlib import Path

from .grouping import ConceptGroup

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
        "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with",
        "how", "what", "why", "when", "is", "are", "it", "this", "that", "you",
        "your", "we", "our", "using", "use", "usage", "getting", "started",
        "installation", "install", "quickstart", "quick", "start", "license",
        "contributing", "changelog", "table", "contents", "overview",
        "introduction", "intro", "example", "examples", "faq", "notes",
        "requirements", "setup", "configuration", "config", "options", "api",
        "reference", "guide", "tutorial", "docs", "documentation", "features",
        "roadmap", "credits", "acknowledgements", "badges", "support",
    }
)

_MIN_TERM_WORDS = 1
_MAX_TERM_WORDS = 4

# A heading that is a sentence is describing something, not naming it.
# "Local paths are masked" and "Headline facts updated 2026-06-22" are both
# real headings from this repository's own docs, and neither is a subsystem.
_SENTENCE_VERBS = frozenset(
    {
        "is", "are", "was", "were", "has", "have", "had", "do", "does", "did",
        "can", "will", "would", "should", "must", "may", "updated", "added",
        "removed", "fixed", "works", "means", "needs", "gets", "goes", "runs",
        "returns", "uses", "supports", "requires", "shows", "makes", "keeps",
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


def extract_terms(repo_root: Path, *, max_terms: int = 200) -> list[str]:
    """Lift candidate subsystem names from the repository's own documentation.

    Returns terms in document order with duplicates removed. Never raises: a
    repository with no docs returns an empty list, which is a supported and
    common outcome, not a degraded one.
    """
    seen: set[str] = set()
    terms: list[str] = []
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
        return []

    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_DOC_BYTES]
        except OSError:
            continue
        for match in list(_HEADING.finditer(text)) + list(_BOLD_TERM.finditer(text)):
            term = _normalise(match.group(1))
            if not _is_useful(term):
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            terms.append(term)
            if len(terms) >= max_terms:
                return terms
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
