"""Check the file citations a model-written module page makes.

Each section of a module page ends with a ``Sources:`` line and the page lists
three files under "Where to start reading". A citation is only worth its place
when the file exists and the section it closes is about that file; a wrong one
sends the reader to the wrong code with the page's authority behind it. This
pass keeps a citation that resolves to a real file (or directory) and that its
section names, by file name or by one of the file's symbols, rewrites it to the
path the rest of the page uses, and drops the rest. Deterministic: no model.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from pathlib import PurePosixPath

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
