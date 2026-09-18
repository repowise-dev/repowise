"""A glossary the team authored, treated as the authority on naming.

Repowise already mines a repository's *house vocabulary*: the terms its own
documents use, corroborated by the structure and rendered as the onboarding
Glossary page (#1276). That path is **descriptive** — "what the repository says
its words mean" — and it stays what it is, deterministic, quote-never-invent and
cited.

This module is the **prescriptive** half. A team that keeps a ubiquitous
language writes one canonical term per concept and lists the synonyms to avoid,
scoped to a bounded context. That file is authored by a person, so it is never
mined, never paraphrased and never ranked against anything: it wins. What this
module does is read it, cite it line by line, and hand it to the same selection
and rendering the mined terms already go through, where a declared term
outranks a mined one and a mined term whose phrase the glossary avoids is
demoted rather than deleted.

The format is the one `grill-with-docs` writes into a root ``CONTEXT.md``:

.. code-block:: markdown

    ## Language

    **Order**:
    A customer's request to purchase one or more products.
    _Avoid_: Purchase, transaction

with ``CONTEXT-MAP.md`` naming per-context ``CONTEXT.md`` files when a
repository has more than one bounded context, and a plain
``| Term | Definition | Avoid |`` table accepted as a fallback for glossaries
that were not written in that dialect. ``GLOSSARY.md`` and ``docs/GLOSSARY.md``
are read the same way.

Everything here is deterministic and offline: the file, the line, and the
team's own sentence about the term. Nothing in this module can invent a
definition, and a declared term with no sentence beside it is ``None`` rather
than a plausible one — the same contract as the miner, for the same reason.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog

from repowise.core.exclusion import build_exclude_spec, is_excluded

from .concept_tree.vocabulary import term_words

log = structlog.get_logger(__name__)

#: Documents that declare vocabulary, relative to the repository root, read in
#: this order. A root ``CONTEXT.md`` is the single-context case; the other two
#: are the names teams reach for when they already kept a glossary before they
#: had a skill writing one.
DECLARED_FILES: tuple[str, ...] = ("CONTEXT.md", "GLOSSARY.md", "docs/GLOSSARY.md")

#: The map naming per-context documents. Its entries are read after the root
#: files, so a repository with a map gets every context's terms and not only
#: whichever one happened to sit at the root.
CONTEXT_MAP_FILE = "CONTEXT-MAP.md"

#: A declared glossary is authored by hand, so it is small. The ceiling exists
#: so a vendored or generated mega-file cannot take the read down.
_MAX_BYTES = 200_000
_MAX_FILES = 12
#: Long enough for two real sentences. The page clamps for display; the parser
#: keeps what was written rather than truncating the authority.
_MAX_DEFINITION_CHARS = 600

#: ``**Term**:`` with the definition optionally on the same line. Anchored at
#: the line start and refusing a leading list marker: ``- **Ordering ->
#: Fulfillment**: ...`` is a relationship line in a context map, not a term,
#: and reading it as one is how a map's prose becomes vocabulary.
_BOLD_TERM = re.compile(r"^\*\*(?P<term>[^*\n]{1,80})\*\*[ \t]*:?[ \t]*(?P<inline>\S.*)?$")
#: ``_Avoid_: a, b`` (also ``**Avoid**:``). Attaches to the term above it.
_AVOID_LINE = re.compile(r"^[\s_*]*avoid[\s_*]*[:：][ \t]*(?P<items>.+)$", re.I)
#: A markdown table row, and the cells inside it.
_TABLE_ROW = re.compile(r"^\s*\|(?P<cells>.+)\|\s*$")
#: A heading, a fence, or a list item: structure rather than a definition.
_SKIP_LINE = re.compile(r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|`{3,}|~{3,})")
#: Cells that name the three columns, folded to lowercase for the test.
_TERM_HEADERS = frozenset({"term", "canonical", "word", "name"})
_DEFINITION_HEADERS = frozenset({"definition", "meaning", "what it means", "what it is"})
_AVOID_HEADERS = frozenset({"avoid", "avoided", "avoided terms", "do not use", "synonyms"})
#: A markdown link inside a context map: ``- [Ordering](./src/ordering/CONTEXT.md)``.
_MD_LINK = re.compile(r"\[(?P<label>[^\]]+)\]\((?P<path>[^)\s]+)")


@dataclass(frozen=True)
class DeclaredTerm:
    """One term a person wrote down as canonical, with where they wrote it.

    ``avoid`` is the list of synonyms the team decided against. It is the whole
    reason a declared glossary is prescriptive rather than descriptive: it is
    the only place in the repository where a word is marked wrong.

    ``definition`` is the team's own sentence, quoted. It is ``None`` when they
    wrote the term and no sentence about it, which is a supported outcome —
    inventing one is the single thing both halves of the glossary refuse to do.

    ``context`` is the bounded context the term belongs to, named by the label
    a ``CONTEXT-MAP.md`` gives it, or ``None`` for a single-context repository.
    """

    term: str
    definition: str | None
    avoid: tuple[str, ...]
    context: str | None
    #: Repository-relative POSIX path of the file the term was read from.
    source_path: str
    #: 1-based line of the ``**Term**:`` line (or the table row), so a reader
    #: can open the file at the sentence rather than at the document.
    line: int


def phrase_key(term: str) -> str:
    """A term's identity, however the gap between its words is spelled.

    "Co-change", "co_change" and "Co change" are one term. This is the same
    equivalence :func:`~.concept_tree.vocabulary.phrase_pattern` uses to match
    a term against prose, so a declared term and a mined term that fold to the
    same key are the same word and are folded into one row.
    """
    return " ".join(term_words(term)).lower()


def _split_avoid(raw: str) -> tuple[str, ...]:
    """The synonyms on an ``_Avoid_:`` line, in the order they were written."""
    parts = [item.strip().strip(".;") for item in re.split(r"[,;]", raw)]
    seen: list[str] = []
    for item in parts:
        if item and item not in seen:
            seen.append(item)
    return tuple(seen)


def _cells(line: str) -> list[str] | None:
    match = _TABLE_ROW.match(line)
    if match is None:
        return None
    return [cell.strip() for cell in match.group("cells").split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(cell and set(cell) <= set("-: ") for cell in cells)


def _table_columns(cells: list[str]) -> tuple[int, int, int | None] | None:
    """``(term, definition, avoid)`` column indexes, or ``None`` for no header."""
    folded = [cell.strip().lower() for cell in cells]
    term = next((i for i, cell in enumerate(folded) if cell in _TERM_HEADERS), None)
    definition = next((i for i, cell in enumerate(folded) if cell in _DEFINITION_HEADERS), None)
    if term is None or definition is None:
        return None
    avoid = next((i for i, cell in enumerate(folded) if cell in _AVOID_HEADERS), None)
    return term, definition, avoid


def _clean_definition(raw: str) -> str | None:
    """One line of prose, or ``None`` when nothing was written."""
    text = " ".join(raw.split()).strip()
    if not text:
        return None
    return text[:_MAX_DEFINITION_CHARS]


def parse_declared_glossary(
    text: str,
    *,
    source_path: str,
    context: str | None = None,
) -> list[DeclaredTerm]:
    """Every term *text* declares, in document order. Never raises.

    Both shapes are read in one pass. The bolded-term form is the one the
    ``grill-with-docs`` skill writes; the table form is what a team that
    already kept a glossary has, and it is read rather than ignored because
    telling such a team their glossary does not count is how a feature like
    this gets switched off.

    A term is only recognised where a term can start: at the beginning of a
    line and never inside a fenced block. Prose that merely mentions a bolded
    phrase mid-sentence is prose, and a list item is a list item.

    The definition is the first paragraph after the term, plus anything on the
    term's own line. A second paragraph is not read: the format puts one or two
    sentences under a term, and running on would eventually swallow the next
    section's prose. ``_Avoid_`` may follow the definition directly.
    """
    terms: list[DeclaredTerm] = []
    seen: set[str] = set()
    columns: tuple[int, int, int | None] | None = None
    in_fence = False
    fence_marker = ""

    #: The term currently being read, and the definition lines gathered for it.
    pending_term: str | None = None
    pending_line = 0
    definition_lines: list[str] = []
    definition_taken = False

    def emit() -> None:
        nonlocal pending_term, definition_lines, definition_taken
        if pending_term is None:
            return
        key = phrase_key(pending_term)
        if key and key not in seen:
            seen.add(key)
            terms.append(
                DeclaredTerm(
                    term=pending_term,
                    definition=_clean_definition(" ".join(definition_lines)),
                    avoid=(),
                    context=context,
                    source_path=source_path,
                    line=pending_line,
                )
            )
        pending_term = None
        definition_lines = []
        definition_taken = False

    def attach_avoid(items: tuple[str, ...]) -> None:
        if not terms or not items:
            return
        last = terms[-1]
        # ``_Avoid_`` belongs to the term above it, which is the only reading
        # the format admits. A second line adds to the first rather than
        # replacing it, so a wrapped list is not silently halved.
        merged = last.avoid + tuple(item for item in items if item not in last.avoid)
        terms[-1] = DeclaredTerm(
            term=last.term,
            definition=last.definition,
            avoid=merged,
            context=last.context,
            source_path=last.source_path,
            line=last.line,
        )

    for number, line in enumerate(text.splitlines(), start=1):
        if in_fence:
            if line.strip().startswith(fence_marker):
                in_fence = False
            continue
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = True
            fence_marker = stripped[:3]
            continue

        cells = _cells(line)
        if cells is None:
            # A line that is not a table row ends any table being read, so the
            # second table in a document is never read with the first one's
            # column map. Without this, an unrelated table's rows become terms.
            columns = None
        else:
            if _is_separator_row(cells):
                continue
            header = _table_columns(cells)
            if header is not None:
                columns = header
                continue
            if columns is None:
                continue
            emit()
            term_i, def_i, avoid_i = columns
            if term_i >= len(cells):
                continue
            term = cells[term_i].strip()
            key = phrase_key(term)
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(
                DeclaredTerm(
                    term=term,
                    definition=(
                        _clean_definition(cells[def_i]) if def_i < len(cells) else None
                    ),
                    avoid=(
                        _split_avoid(cells[avoid_i])
                        if avoid_i is not None and avoid_i < len(cells)
                        else ()
                    ),
                    context=context,
                    source_path=source_path,
                    line=number,
                )
            )
            continue

        avoid_match = _AVOID_LINE.match(line)
        if avoid_match:
            emit()
            attach_avoid(_split_avoid(avoid_match.group("items")))
            continue

        term_match = _BOLD_TERM.match(line)
        if term_match:
            emit()
            term = term_match.group("term").strip().strip(":：").strip()
            inline = (term_match.group("inline") or "").strip()
            if term and not _SKIP_LINE.match(term):
                pending_term = term
                pending_line = number
                if inline:
                    # A one-line definition closes the term's paragraph, so a
                    # following line is the next paragraph and not more of it.
                    definition_lines = [inline]
                    definition_taken = True
            continue

        if pending_term is None:
            continue
        if not stripped:
            # A blank line ends the paragraph. The term stays open for a
            # trailing ``_Avoid_:`` line, which is the one thing the format
            # allows to follow the definition.
            if definition_lines:
                definition_taken = True
            continue
        if _SKIP_LINE.match(line):
            continue
        if not definition_taken and not definition_lines:
            definition_lines = [stripped]

    emit()
    return terms


def _context_map_entries(text: str, repo_root: Path) -> list[tuple[str, Path]]:
    """``(label, path)`` for every markdown link a context map names."""
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for match in _MD_LINK.finditer(text):
        label = match.group("label").strip()
        raw = match.group("path").strip().strip("<>")
        if not label or not raw or raw.startswith(("http://", "https://", "#")):
            continue
        target = (repo_root / raw).resolve()
        key = str(target)
        if key in seen or target.suffix.lower() != ".md":
            continue
        seen.add(key)
        found.append((label, target))
    return found


def _readable(path: Path, repo_root: Path, spec: object) -> str | None:
    """The file's text, or ``None`` when it may not be read. Never raises."""
    try:
        # A path the repository excludes from its own index is scratch work,
        # not a declaration. The same rule the miner applies to its sources.
        rel = path.relative_to(repo_root).as_posix()
    except ValueError:
        return None
    if is_excluded(rel, spec):
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:_MAX_BYTES]
    except OSError:
        log.warning("declared_glossary.unreadable", path=rel)
        return None


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:  # pragma: no cover - every path came from repo_root
        return path.name


def load_declared_glossary(
    repo_root: Path | str,
    *,
    max_files: int = _MAX_FILES,
) -> list[DeclaredTerm]:
    """Every term the repository declares, in the order the files are read.

    Reads the root documents, then whatever a ``CONTEXT-MAP.md`` names, so a
    multi-context repository contributes every context's terms with the label
    the map gave each one. A repository with no declared glossary returns an
    empty list, which is the common case and not a failure.

    Never raises: an unreadable file costs its terms and nothing else.
    """
    root = Path(repo_root)
    try:
        spec = build_exclude_spec(root)
    except Exception as exc:
        log.warning("declared_glossary.exclusion_unreadable", error=str(exc))
        spec = None

    candidates: list[tuple[str | None, Path]] = []
    for name in DECLARED_FILES:
        candidates.append((None, root / name))

    map_path = root / CONTEXT_MAP_FILE
    text = _readable(map_path, root, spec)
    if text:
        candidates.extend(_context_map_entries(text, root))

    declared: list[DeclaredTerm] = []
    read: list[str] = []
    seen_paths: set[str] = set()
    for context, path in candidates:
        if len(read) >= max_files:
            break
        resolved = str(path)
        if resolved in seen_paths or not path.is_file():
            continue
        seen_paths.add(resolved)
        body = _readable(path, root, spec)
        if body is None:
            continue
        read.append(_rel(path, root))
        declared.extend(
            parse_declared_glossary(body, source_path=_rel(path, root), context=context)
        )

    if declared:
        log.info(
            "declared_glossary.loaded",
            files=len(read),
            terms=len(declared),
            sources=read,
        )
    return declared


def declared_avoid_index(declared: Sequence[DeclaredTerm]) -> dict[str, str]:
    """``{folded synonym: canonical term}`` across every declared term.

    Folded, because "Purchase" and "purchase" are the same synonym to a reader
    even though they are two keys to a dict. The canonical spelling is the
    value, so a demoted mined term can say which word to use instead.
    """
    index: dict[str, str] = {}
    for term in declared:
        for synonym in term.avoid:
            key = phrase_key(synonym)
            if key:
                index.setdefault(key, term.term)
    return index
