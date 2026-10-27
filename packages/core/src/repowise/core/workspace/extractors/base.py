"""Shared file-scanning primitives for contract extractors.

Both the HTTP and gRPC extractors walk a repository the same way and read each
candidate file as UTF-8. That traversal lives here once so the per-contract-type
orchestrators only declare *which* extensions they care about. Discovery is
delegated to the ingestion :class:`~repowise.core.ingestion.traverser.FileTraverser`
so the scanned file set respects ``.gitignore`` and nested-repo boundaries
(identical to the repo's actual index) instead of a raw ``os.walk`` that would
descend into sibling/vendored repos rooted under the workspace.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING

from repowise.core.test_paths import is_skippable_test_path

from .langs import APEX, CSHARP, GO, JAVA, JS_TS, KOTLIN, PHP, PROTO, RUST

if TYPE_CHECKING:
    from repowise.core.workspace.repo_index import RepoIndex

# Repowise's own contract-extractor sources. Their docstrings and comments spell
# out the syntax each dialect matches (``@app.get("/path")``, ``topic::orders``,
# ``AuthServiceStub``), so scanning them yields contracts for endpoints and
# queues that do not exist — on this repo, 14 of them. A match in a comment is
# dropped (see :meth:`ScanContext.in_comment`), but one in a docstring is a
# string to the mask; excluding the tree that is nothing *but* examples covers it.
_SELF_EXCLUDE_GLOBS = ("*/repowise/core/workspace/extractors/*",)


def line_at(content: str, offset: int) -> int:
    """The 1-indexed line of *content* holding character *offset*.

    Every regex dialect reports where it matched this way, which is what binds
    a contract to a symbol id (see :func:`..contracts.bind_symbol_ids`).
    """
    return content.count("\n", 0, offset) + 1


def make_exclude_predicate(
    extra_globs: tuple[str, ...] = (),
    *,
    exclude_tests: bool = True,
) -> Callable[[str], bool]:
    """Build a ``rel_path -> bool`` skip predicate for contract extraction.

    Skips test material per :func:`is_skippable_test_path` (unless
    *exclude_tests* is False): a route handler or topic publisher that exists
    only there is a fixture, not a real service contract. Also skips the
    extractors' own source tree (see :data:`_SELF_EXCLUDE_GLOBS`), plus any
    user-supplied ``extra_globs`` (matched against the full POSIX path and the
    bare filename).
    """
    globs = _SELF_EXCLUDE_GLOBS + tuple(extra_globs)

    def skip(rel_path: str) -> bool:
        if exclude_tests and is_skippable_test_path(rel_path):
            return True
        name = rel_path.rsplit("/", 1)[-1]
        return any(fnmatch(rel_path, g) or fnmatch(name, g) for g in globs)

    return skip


#: Where comments are ``//`` and ``/* */`` and ``#`` is code (a C# directive, a
#: Rust or PHP attribute). Ruby is left out: its ``/.../`` regex literals would
#: open a comment the masker cannot close. VB and SQL are left out too. A match
#: in an unmasked file is never treated as commented out.
_SLASH_COMMENT_SUFFIXES = JAVA | KOTLIN | GO | CSHARP | RUST | PHP | APEX | PROTO | {".prisma"}
_XML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _comment_mask(suffix: str, content: str) -> str | None:
    """*content* with its comments blanked and offsets kept; None for an unknown syntax."""
    from repowise.core.ingestion.source_mask import mask_source

    # The JS-aware masker reads regex literals and Python string prefixes.
    if suffix == ".py":
        return mask_source(content, suffix)
    if suffix in JS_TS:
        return mask_source(content, ".js")
    if suffix == ".xml":
        return _XML_COMMENT_RE.sub(lambda m: " " * len(m.group(0)), content)
    if suffix not in _SLASH_COMMENT_SUFFIXES:
        return None
    from repowise.core.analysis.security.masking import mask_comments_and_strings, mask_dialect

    return mask_comments_and_strings(
        content, strings=False, hash_comments=False, **mask_dialect(suffix)
    )


class _FileMemo:
    """What one file's dialects share: each gate token's presence, the comment mask,
    and the offsets of calls located but not named."""

    __slots__ = ("lines", "mask", "masked", "tokens", "unresolved")

    def __init__(self) -> None:
        self.tokens: dict[str, bool] = {}
        self.masked = False
        self.mask: str | None = None
        self.lines: tuple[list[str], list[str]] | None = None
        self.unresolved: set[int] = set()


@dataclass(frozen=True)
class ScanContext:
    """A single source file presented to a dialect.

    ``rel_path`` is POSIX-relative to the repo root; ``suffix`` is the
    lower-cased file extension (including the dot); ``content`` is the decoded
    file text. ``mounts`` is the repo-wide map the HTTP dialects collect
    before extraction: router mount prefixes, where each app serves its
    routes, and client instances other files import (see
    :mod:`.http.mounts`); empty for single-file extraction.

    ``index`` is the repo's read-only symbol table, when the repo has one.
    Every dialect may ignore it and read ``content`` exactly as before.

    Gates and comments are read here once per file, however many dialects
    ask: :meth:`has_any` memoizes each token, and the comment mask is built
    the first time a match asks about it, so a file nothing matches in never
    pays for it.
    """

    repo_alias: str
    rel_path: str
    suffix: str
    content: str
    mounts: Mapping[str, str] = field(default_factory=dict)
    index: RepoIndex | None = None
    _memo: _FileMemo = field(default_factory=_FileMemo, init=False, repr=False, compare=False)

    def has_any(self, tokens: tuple[str, ...]) -> bool:
        """Whether the file contains one of *tokens*, each searched for once per file."""
        seen = self._memo.tokens
        for token in tokens:
            found = seen.get(token)
            if found is None:
                found = seen[token] = token in self.content
            if found:
                return True
        return False

    def note_unresolved(self, offset: int) -> None:
        """Record a call at *offset* whose destination this file cannot settle."""
        self._memo.unresolved.add(offset)

    @property
    def unresolved_calls(self) -> int:
        """Calls :meth:`note_unresolved` recorded: one per site, however many dialects read it."""
        return len(self._memo.unresolved)

    def _mask(self) -> str | None:
        memo = self._memo
        if not memo.masked:
            memo.mask = _comment_mask(self.suffix, self.content)
            memo.masked = True
        return memo.mask

    def in_comment(self, offset: int) -> bool:
        """Whether a match at *offset* (leading whitespace skipped) sits in a comment."""
        content = self.content
        end = len(content)
        while offset < end and content[offset] in " \t\n":
            offset += 1
        if offset >= end:
            return False
        mask = self._mask()
        return mask is not None and mask[offset] != content[offset]

    def comment_only_line(self, line: int | None) -> bool:
        """Whether 1-based *line* holds a comment and nothing else."""
        if line is None or line < 1:
            return False
        mask = self._mask()
        if mask is None:
            return False
        memo = self._memo
        if memo.lines is None:
            memo.lines = (mask.split("\n"), self.content.split("\n"))
        code, source = memo.lines
        if line > len(code) or code[line - 1].strip():
            return False
        return bool(source[line - 1].strip())


# One scanned source file: ``(rel_path, suffix, content)``.
SourceFile = tuple[str, str, str]


def select_files(
    repo_path: Path,
    extensions: frozenset[str],
    exclude: Callable[[str], bool] | None,
    files: Sequence[SourceFile] | None,
) -> list[SourceFile]:
    """Filter an already-walked *files* list, or walk *repo_path* when it is None.

    Five extractors each used to walk the repo independently, so a repo was
    traversed and every matching file re-read once per extractor. The
    orchestrator now walks once and passes the result down; *files* being None
    keeps the standalone call (and every direct test) working unchanged.
    """
    if files is None:
        return list(iter_source_files(repo_path, extensions, exclude))
    return [f for f in files if f[1] in extensions]


def iter_source_files(
    repo_root: Path,
    extensions: frozenset[str],
    exclude: Callable[[str], bool] | None = None,
) -> Iterator[tuple[str, str, str]]:
    """Yield ``(rel_path, suffix, content)`` for each scannable source file.

    File discovery is delegated to the ingestion :class:`FileTraverser`, the
    same gitignore- and nested-repo-aware walker the main index uses, rather
    than a raw ``os.walk``. This matters whenever a workspace repo's root
    physically contains *other* git repositories (sibling/vendored repos,
    benchmark clones, build worktrees) or large gitignored trees: a naive walk
    descends into all of them and scans hundreds of thousands of foreign files.
    That is not hypothetical: on a developer checkout whose root held nested
    repos, the old walk yielded **1.05M files** for a repo whose real source is
    ~2k, wedging contract extraction for tens of minutes. FileTraverser prunes
    nested git repos and honours ``.gitignore``, so the scanned set matches the
    repo's actual index.

    Only files whose lower-cased suffix is in *extensions* are yielded.
    Oversized, binary, generated, and ignored files are already excluded by the
    traverser. Unreadable files are silently skipped.

    Files are read as bytes and decoded here rather than via ``read_text``; the
    newline translation ``read_text`` would have applied is reproduced verbatim
    below, leaving every dialect's input unchanged.
    """
    from repowise.core.ingestion.traverser import FileTraverser

    root = Path(repo_root).resolve()
    if not root.is_dir():
        return

    # A contract format no language parses (`schema.prisma`) is still read here.
    traverser = FileTraverser(root, keep_unparsed=extensions)
    for info in traverser.traverse():
        suffix = os.path.splitext(info.path)[1].lower()
        if suffix not in extensions:
            continue
        if exclude is not None and exclude(info.path):
            continue
        try:
            raw = Path(info.abs_path).read_bytes()
        except OSError:
            continue
        content = raw.decode("utf-8", errors="replace")
        # Universal newlines, as ``read_text`` applied before this read moved
        # to bytes. Dialect regexes are written against ``\n``.
        if "\r" in content:
            content = content.replace("\r\n", "\n").replace("\r", "\n")
        yield info.path, suffix, content
