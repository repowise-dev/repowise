"""Check the file citations a model-written module page makes.

Each section of a module page ends with a ``Sources:`` line and the page lists
three files under "Where to start reading". A citation is only worth its place
when the file exists and the section it closes is about that file; a wrong one
sends the reader to the wrong code with the page's authority behind it. This
pass keeps a citation that resolves to a real file (or directory) and that its
section names, by file name or by one of the file's symbols, rewrites it to the
path the rest of the page uses, and drops the rest. Deterministic: no model.

The same module owns the other half of a citation: the line span it points at
(checked against the spans the page was written from) and the link it opens
(the repository's forge at the commit the page was generated at).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

import structlog

from .agent_digest import h2_sections
from .context.module_facts import relative_to

log = structlog.get_logger(__name__)

_SOURCES_RE = re.compile(r"^\s*(?:[*_]{1,2})?Sources?:(?:[*_]{1,2})?\s*(.*)$", re.IGNORECASE)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_LINE_REF_RE = re.compile(r":\d+(?:-\d+)?$")
_START_BULLET_RE = re.compile(r"^(\s*[-*]\s+)`([^`]+)`(.*)$")
_MAX_SOURCES = 3
_MIN_STEM = 3
_MIN_SYMBOL = 4


class _Resolver:
    """Maps a cited path to the repository file or directory it names."""

    def __init__(self, base: str, members: Collection[str], known: Collection[str]) -> None:
        self._base = base
        self._members = sorted(members)
        self._known = set(known) | set(members)
        self._dirs = {
            str(parent)
            for path in self._known
            for parent in PurePosixPath(path).parents
            if str(parent) != "."
        }

    def resolve(self, cited: str) -> str | None:
        item = _LINE_REF_RE.sub("", cited.strip().strip("./")).rstrip("/")
        if not item:
            return None
        is_dir = cited.strip().endswith("/")
        rooted = f"{self._base}/{item}" if self._base else item
        for candidate in (rooted, item):
            if not is_dir and candidate in self._known:
                return candidate
            if candidate in self._dirs:
                return f"{candidate}/"
        if is_dir:
            return None
        return self._unique_suffix(item, self._members) or self._unique_suffix(item, self._known)

    @staticmethod
    def _unique_suffix(item: str, paths: Iterable[str]) -> str | None:
        hits = [p for p in paths if p == item or p.endswith(f"/{item}")]
        return hits[0] if len(hits) == 1 else None


def _spellings(name: str) -> set[str]:
    """How prose writes a file or directory name: as is, and with its separators as spaces."""
    name = name.lower()
    spaced = re.sub(r"[_\-.]+", " ", name).strip()
    return {name, spaced, spaced.removesuffix("s")} - {""}


def _named_in(
    path: str, text: str, base: str, symbols: Mapping[str, Collection[str]]
) -> bool:
    """Whether *text* names the file at *path*, its part, or one of its symbols.

    A path written out in full, from the repository root or from the module,
    counts. Names match whole words only, so a short stem such as ``spec`` is
    not found in "specific".
    """
    bare = path.rstrip("/").lower()
    if bare in text or relative_to(bare, base.lower()) in text:
        return True
    pure = PurePosixPath(path.rstrip("/"))
    names = {pure.name}
    if len(pure.stem) >= _MIN_STEM:
        names.add(pure.stem)
    if str(pure.parent) not in (base, "."):
        names.add(pure.parent.name)
    names.update(n for n in symbols.get(path, ()) if len(n) >= _MIN_SYMBOL)
    return any(
        re.search(rf"(?<!\w){re.escape(word)}(?!\w)", text)
        for name in names
        for word in _spellings(name)
    )


def lint_sources(
    content: str,
    *,
    base: str,
    members: Collection[str],
    known_paths: Collection[str],
    symbols_by_file: Mapping[str, Collection[str]],
) -> str:
    """*content* with every ``Sources:`` line and start-reading bullet checked."""
    resolver = _Resolver(base, members, known_paths)
    out: list[str] = []
    for heading, lines in h2_sections(content):
        start_reading = heading is not None and heading.lower().startswith("where to start")
        prose = "\n".join(line for line in lines if not _SOURCES_RE.match(line)).lower()
        for line in lines:
            if start_reading and (bullet := _START_BULLET_RE.match(line)):
                path = resolver.resolve(bullet.group(2))
                if path:
                    out.append(f"{bullet.group(1)}`{relative_to(path, base)}`{bullet.group(3)}")
                else:
                    log.info("page_sources.dropped", cited=bullet.group(2), resolved="")
                continue
            match = _SOURCES_RE.match(line) if heading is not None else None
            if not match:
                out.append(line)
                continue
            cited = _BACKTICK_RE.findall(match.group(1)) or match.group(1).split(",")
            kept: list[str] = []
            for item in cited:
                path = resolver.resolve(item)
                shown = relative_to(path, base) if path else ""
                if path and shown not in kept and _named_in(path, prose, base, symbols_by_file):
                    kept.append(shown)
                elif not path or shown not in kept:
                    # One line per dropped citation; the run log shows how often it happens.
                    log.info("page_sources.dropped", cited=item.strip(), resolved=path or "")
            if kept:
                out.append("Sources: " + ", ".join(f"`{p}`" for p in kept[:_MAX_SOURCES]))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


# ---------------------------------------------------------------------------
# Line spans: what a citation points at inside the file
# ---------------------------------------------------------------------------

#: A symbol's span in a file: ``(start_line, end_line, symbol name)``.
Span = tuple[int, int, str]

# A path holds a ``/`` or a ``.``, so `localhost:8080` is not a citation.
_SPAN_CITE_RE = re.compile(r"`([^`\s]*[/.][^`\s]*?):(\d+)(?:-(\d+))?`")


def source_entry(
    path: str,
    kind: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    symbol: str | None = None,
) -> dict[str, object]:
    """One row of a page's ``metadata["sources"]``: the shape every reader takes.

    Lines are present only when known, so a whole-file citation stays
    ``{path, kind}`` exactly as before.
    """
    entry: dict[str, object] = {"path": path, "kind": kind}
    if start_line:
        entry["start_line"] = start_line
        entry["end_line"] = end_line if end_line and end_line >= start_line else start_line
    if symbol:
        entry["symbol"] = symbol
    return entry


def _containing_span(spans: Iterable[Span], start: int, end: int) -> Span | None:
    """The narrowest known span that overlaps ``start..end``."""
    hits = [s for s in spans if s[0] <= end and start <= s[1]]
    return min(hits, key=lambda s: (s[1] - s[0], s[0])) if hits else None


def ground_span_citations(
    content: str, spans_by_file: Mapping[str, Iterable[Span]]
) -> tuple[str, list[dict[str, object]]]:
    """Check every `` `path:12-40` `` citation against the evidence's spans.

    A citation whose file is in the evidence and whose range overlaps one of
    that file's spans is rewritten to the span itself, so a reader lands on
    the whole function rather than the lines the model guessed. One whose file
    or range matches nothing keeps the path and loses the numbers: a wrong
    line is worse than none. Returns the content and the grounded citations as
    :func:`source_entry` rows, first mention first.
    """
    files = {path: list(spans) for path, spans in spans_by_file.items()}
    cited: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()

    def ground(match: re.Match[str]) -> str:
        path = _unique_file(match.group(1).removeprefix("./"), files)
        start = int(match.group(2))
        end = int(match.group(3) or start)
        span = _containing_span(files.get(path or "", ()), start, end)
        if path is None or span is None:
            log.info("page_sources.span_dropped", cited=match.group(0))
            return f"`{path or match.group(1)}`"
        if (path, span[0]) not in seen:
            seen.add((path, span[0]))
            cited.append(
                source_entry(path, "cited", start_line=span[0], end_line=span[1], symbol=span[2])
            )
        return f"`{path}:{span[0]}-{span[1]}`"

    return _SPAN_CITE_RE.sub(ground, content), cited


def _unique_file(cited: str, files: Collection[str]) -> str | None:
    """The evidence file *cited* names, written in full or as a unique suffix."""
    if cited in files:
        return cited
    hits = [p for p in files if p.endswith(f"/{cited}")]
    return hits[0] if len(hits) == 1 else None


def symbol_spans(file_contexts: Iterable[Any]) -> dict[str, list[Span]]:
    """``file -> spans`` of every symbol the assembled file contexts carry."""
    out: dict[str, list[Span]] = {}
    for fc in file_contexts:
        spans = [span for span in map(_symbol_span, fc.symbols) if span is not None]
        if spans:
            out[fc.file_path] = spans
    return out


def _symbol_span(sym: Mapping[str, Any]) -> Span | None:
    start, end = sym.get("start_line"), sym.get("end_line")
    if not isinstance(start, int) or start <= 0 or not sym.get("name"):
        return None
    return (start, end if isinstance(end, int) else start, sym["name"])


def module_page_sources(
    content: str,
    *,
    base: str,
    members: Collection[str],
    known_paths: Collection[str],
    file_contexts: Iterable[Any],
) -> list[dict[str, object]]:
    """A module page's ``sources``: the spans its kept citations stand for."""
    return section_symbol_spans(
        content,
        base=base,
        members=members,
        known_paths=known_paths,
        spans_by_file=symbol_spans(file_contexts),
    )


def _symbol_spellings(token: str) -> set[str]:
    """``Coder.run()`` names ``Coder.run`` and ``run``; prose writes symbols both ways."""
    bare = token.strip().removesuffix("()")
    return {bare, re.split(r"\.|::", bare)[-1]}


def section_symbol_spans(
    content: str,
    *,
    base: str,
    members: Collection[str],
    known_paths: Collection[str],
    spans_by_file: Mapping[str, Iterable[Span]],
) -> list[dict[str, object]]:
    """The symbol spans a page's ``Sources:`` lines stand for.

    A ``Sources:`` line names files; the section above it names the functions
    it is about. Each cited file contributes the spans of its symbols that the
    section names in backticks, so the citation lands on the code the claim is
    about. Run after :func:`lint_sources`, which has already dropped the
    citations their section does not support.
    """
    resolver = _Resolver(base, members, known_paths)
    out: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()
    for _heading, lines in h2_sections(content):
        named = _named_symbols(lines)
        for path in _cited_paths(lines, resolver):
            out.extend(_named_spans(path, spans_by_file.get(path, ()), named, seen))
    return out


def _named_spans(
    path: str, spans: Iterable[Span], named: set[str], seen: set[tuple[str, int]]
) -> list[dict[str, object]]:
    """Rows for the spans in *path* whose symbol the section names, once each."""
    rows: list[dict[str, object]] = []
    for start, end, symbol in spans:
        if symbol in named and (path, start) not in seen:
            seen.add((path, start))
            rows.append(source_entry(path, "cited", start_line=start, end_line=end, symbol=symbol))
    return rows


def _named_symbols(lines: Iterable[str]) -> set[str]:
    """Every symbol a section's prose names in backticks, in both spellings."""
    return {
        name
        for line in lines
        if not _SOURCES_RE.match(line)
        for token in _BACKTICK_RE.findall(line)
        for name in _symbol_spellings(token)
    }


def _cited_paths(lines: Iterable[str], resolver: _Resolver) -> list[str]:
    """The files a section's ``Sources:`` lines resolve to, in order."""
    paths: list[str] = []
    for line in lines:
        match = _SOURCES_RE.match(line)
        if match:
            resolved = (resolver.resolve(item) for item in _BACKTICK_RE.findall(match.group(1)))
            paths.extend(p for p in resolved if p)
    return paths


# ---------------------------------------------------------------------------
# Links: where a citation opens
# ---------------------------------------------------------------------------


def attach_source_links(pages: Iterable[Any], repo_path: Any) -> int:
    """Stamp the commit and, when the repo has a known remote, a forge URL per source.

    The commit is the checkout's HEAD at generation, which is the commit the
    line numbers were read at: a link built from it keeps pointing at the
    lines the page cites after the code moves on. Without a forge (no remote,
    or a host no forge claims) only the commit is stamped and the reader links
    to its own file view instead. Returns how many sources got a URL.
    """
    from repowise.core.forges import get_forge, read_remote_ref
    from repowise.core.git_head import read_head_commit

    commit = read_head_commit(repo_path) if repo_path else None
    if not commit:
        return 0
    ref = read_remote_ref(repo_path)
    forge = get_forge(ref.forge) if ref is not None else None
    linked = 0
    for page in pages:
        sources = (getattr(page, "metadata", None) or {}).get("sources")
        if isinstance(sources, list) and sources:
            page.metadata["sources_commit"] = commit
            linked += _link_sources(sources, forge, ref, commit)
    return linked


def _link_sources(sources: list[dict], forge: Any, ref: Any, commit: str) -> int:
    linked = 0
    for source in sources:
        url = _blob_url(forge, ref, commit, source)
        if url:
            source["url"] = url
            linked += 1
    return linked


def _blob_url(forge: Any, ref: Any, commit: str, source: Mapping[str, Any]) -> str:
    path = source.get("path")
    linkable = isinstance(path, str) and path and not path.endswith("/")
    if forge is None or not linkable:
        return ""
    start = source.get("start_line")
    end = source.get("end_line")
    return forge.blob_url(
        ref,
        commit,
        path,
        start if isinstance(start, int) else None,
        end if isinstance(end, int) else None,
    )
