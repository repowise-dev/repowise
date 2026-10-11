"""The file's own vocabulary: the words a question about this file would use.

Not to be confused with ``concept_tree/vocabulary.py``, which mines doc headings
and binds repo-level terms to concept groups. Those terms are the same on every
file page in the repository and so carry no discriminative power between files,
which is the reason this per-file module exists alongside it. The two are not
duplicates and must not be merged.

A file page renders an overview, a table of symbols and a list of dependency
paths. None of those carry the words a reader actually searches with: a flag
name, an error message, a struct field, the phrasing of a doc comment. So a
page can describe a file accurately and still be unreachable by any question
about what the file does.

Measured, on `pkg/cmd/release/list/list.go` in cli/cli. The source declares
``Order string``, registers the flag as ``"order"`` with the help text
``"Order of releases returned"``, and builds a GraphQL query naming
``CREATED_AT``. Its page carried a path header, two symbol names and 29
dependency paths, and not one of those tokens. A question asking how to order
a release list by creation date had nothing to match.

This module returns a bounded bag of that vocabulary, for the page to render
and the index to embed. Three properties are deliberate:

* **Ordered by how distinguishing it is, because it is capped.** Declared
  names first, then literals, then comment prose, then everything else. A cap
  that truncates the tail should truncate the least specific material.
* **Deduplicated and lowercased into words.** ``ExcludePreReleases`` is stored
  as itself and as ``exclude``, ``pre``, ``releases``, because a question is
  asked in words, not in camel case.
* **Vocabulary, not a code listing.** No structure, no bodies, no ordering
  that implies control flow. Anyone wanting the code should open the file, and
  the page says where it is.
"""

from __future__ import annotations

import re

# A cap, not a budget. Roughly the size of the rest of a file page, so this
# section can matter without dominating what a reader sees.
_MAX_CHARS = 1200

# Two or more characters so single-letter receivers and loop variables do not
# fill the bag; they are never what a question is about.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

# Quoted text, both quote styles, bounded at both ends. The lower bound drops
# punctuation and separators; the upper bound drops embedded blobs, SQL and
# minified data, which are long, unsearchable and would eat the whole cap.
_STRING = re.compile(r'"([^"\\\n]{3,60})"' r"|'([^'\\\n]{3,60})'")

# An indented, capitalised name followed by a type: a Go struct field, and
# close enough to a TypeScript interface member to be worth the same pattern.
_GO_FIELD = re.compile(r"^[ \t]+([A-Z][A-Za-z0-9_]*)\s+[\w\*\[\]\.\{\}]+", re.MULTILINE)

# An indented assignment or annotation: a Python class attribute or dataclass
# field. Matches some locals too, which is acceptable; a local name is still
# vocabulary from this file.
_PY_ATTR = re.compile(r"^[ \t]{4,}(?:self\.)?([a-z_][a-z0-9_]*)\s*[:=][^=]", re.MULTILINE)

# A whole-line comment. Trailing comments are skipped on purpose: they annotate
# a statement and tend to be fragments, where a leading comment is usually a
# sentence about the thing below it.
_COMMENT = re.compile(r"^[ \t]*(?://|#)[ \t]?(.{4,120})$", re.MULTILINE)


def _words(token: str) -> list[str]:
    """``ExcludePreReleases`` and ``exclude_pre_releases`` into their words."""
    out: list[str] = []
    for part in re.split(r"_+", token):
        out.extend(re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", part))
    return [w.lower() for w in out if len(w) > 2]


def file_vocabulary(source: str, *, max_chars: int = _MAX_CHARS) -> str:
    """The bounded vocabulary of one source file, most distinguishing first.

    Returns a plain space-joined string. Empty when *source* yields nothing,
    so the caller can drop the whole section rather than render a heading with
    nothing under it.
    """
    if not source:
        return ""

    seen: set[str] = set()
    bag: list[str] = []
    budget = max_chars

    def add(term: str) -> bool:
        """Append if new and affordable. False once the cap is reached."""
        nonlocal budget
        term = term.strip()
        if not term or term.lower() in seen:
            return True
        cost = len(term) + 1
        if cost > budget:
            return False
        seen.add(term.lower())
        bag.append(term)
        budget -= cost
        return True

    # 1. Declared member names. The most specific thing a file contains that
    #    its symbol table does not already name.
    for match in _GO_FIELD.findall(source) + _PY_ATTR.findall(source):
        if not add(match):
            return " ".join(bag)
        for word in _words(match):
            if not add(word):
                return " ".join(bag)

    # 2. String literals: flag names, error text, help strings, route
    #    templates. This is where user-facing wording lives, which is the
    #    wording a bug report repeats back.
    for double, single in _STRING.findall(source):
        if not add(double or single):
            return " ".join(bag)

    # 3. Comment prose, the file's own sentences about itself.
    for comment in _COMMENT.findall(source):
        if not add(comment):
            return " ".join(bag)

    # 4. Everything else, as words. Deliberately last: an identifier that
    #    matters is usually already above, and this tail is the part a cap
    #    should be free to cut.
    for token in _IDENT.findall(source):
        for word in _words(token):
            if not add(word):
                return " ".join(bag)

    return " ".join(bag)
