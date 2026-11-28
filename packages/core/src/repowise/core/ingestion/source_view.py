"""The repository files a server can read at serving time.

A tool that serves live source (a symbol body, a preview, the lines a search
matched) reads it through a :class:`SourceView`. The one implementation here,
:class:`DiskSource`, is the checkout on disk. A server with no checkout serves
``None`` in its place, and every such field then says where to read instead.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

#: How long one :meth:`DiskSource.grep` may run before it answers with nothing.
GREP_SECONDS = 5.0


class SourceView(Protocol):
    """Read access to a repository's files, by repo-relative path."""

    def read(self, rel: str, *, allow_extracted: bool = False) -> str | None:
        """The file's text with newlines normalised, or ``None`` when it cannot be served."""
        ...

    def grep(
        self,
        pattern: str,
        *,
        paths: list[str] | None = None,
        ignore_case: bool = False,
        limit: int,
    ) -> list[tuple[str, int, str]]:
        """``(rel, line, text)`` for lines matching the extended regex *pattern*.

        *paths* are pathspecs that narrow the search. At most *limit* lines,
        and ``[]`` when the search cannot run.
        """
        ...


class DiskSource:
    """The checkout at *root*: files through the core source reader, search through ``git grep``."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(str(root))

    def read(self, rel: str, *, allow_extracted: bool = False) -> str | None:
        """Refuses any path that resolves outside the root: an index row is not
        a trust boundary. Decoding is lossy on purpose (``errors="replace"``),
        so a latin-1 file degrades one field instead of failing the call.
        Extraction failures read as unavailable.
        """
        from .document_extractors import DocumentExtractionError
        from .source_reader import read_source

        try:
            source = read_source(self.root, rel, allow_extracted=allow_extracted)
            if source is None:
                return None
            return source.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        except (OSError, ValueError, DocumentExtractionError):
            return None

    def grep(
        self,
        pattern: str,
        *,
        paths: list[str] | None = None,
        ignore_case: bool = False,
        limit: int,
    ) -> list[tuple[str, int, str]]:
        """Tracked text files only (``-I``), bounded by :data:`GREP_SECONDS`."""
        args = ["git", "--no-pager", "grep", "--no-color", "-n", "-I"]
        if ignore_case:
            args.append("-i")
        args += ["-E", pattern, "--", *(paths or ())]
        try:
            proc = subprocess.run(
                args,
                cwd=str(self.root),
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                # A stdio MCP server's stdin is the protocol stream: never let git read it.
                stdin=subprocess.DEVNULL,
                timeout=GREP_SECONDS,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError):
            return []
        out: list[tuple[str, int, str]] = []
        for raw in proc.stdout.splitlines()[:limit]:
            path, _, rest = raw.partition(":")
            num, _, text = rest.partition(":")
            if num.isdigit():
                out.append((path, int(num), text))
        return out
