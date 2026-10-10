"""Code that runs Alembic loads every migration script by path.

``alembic.command.upgrade(config, "head")``, ``alembic.config.main(...)`` and
an ``alembic upgrade`` subprocess all import ``env.py`` and each file in the
script directory's ``versions/`` from the filesystem, so the code running them
has no import edge to any of them, and a test that migrates a database
reaches none of the migrations it runs.

Any code file (Python, JavaScript, TypeScript) whose code names ``alembic`` (an import, an attribute, a command
line in a string; comments and docstrings aside) gets an edge to every file
of every script directory: a directory holding ``env.py`` beside a
``versions/`` directory. A helper that runs the command passes it on to the
tests importing the helper through their ordinary import edges. Over-claiming
on a file that names Alembic without running it costs only a larger
selection.

Ceilings: ``version_locations`` outside ``<script dir>/versions`` and nested
version directories are not followed, so a migration there has no edge and a
change to it keeps its full run; every script directory in the repository is
linked to every runner.
"""

from __future__ import annotations

import io
import posixpath
import re
import tokenize
from typing import TYPE_CHECKING, Any

from ..languages.python_strings import live_text
from ..resolvers import ResolverContext
from ..source_text import source_bytes
from .base import DetectionContext, FrameworkHandler, _add_edge_if_new
from .test_path_strings import _CODE_EXTS

if TYPE_CHECKING:
    import networkx as nx

# Stamped on the edge so consumers that mean "imports" can tell it apart.
ALEMBIC_RUNNER_HINT = "alembic_runner"

_WORD = re.compile(rb"(?<![\w.-])alembic(?![\w-])", re.IGNORECASE)
# Python 3.12+ tokenizes an f-string's literal text as its own token type.
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)


def script_files(path_set: set[str]) -> list[str]:
    """``env.py`` and the migrations of every Alembic script directory in *path_set*."""
    parents = {
        posixpath.dirname(posixpath.dirname(p))
        for p in path_set
        if p.endswith(".py") and posixpath.basename(posixpath.dirname(p)) == "versions"
    }
    scripts = {d for d in parents if posixpath.join(d, "env.py") in path_set}
    return sorted(p for p in path_set if _in_script_dir(p, scripts))


def _in_script_dir(path: str, scripts: set[str]) -> bool:
    """A migration in ``<script dir>/versions/``, or the script directory's ``env.py``."""
    folder = posixpath.dirname(path)
    if posixpath.basename(path) == "env.py":
        return folder in scripts
    return (
        path.endswith(".py")
        and posixpath.basename(folder) == "versions"
        and posixpath.dirname(folder) in scripts
    )


def names_alembic(path: str, blob: bytes) -> bool:
    """Whether *blob*'s code (not its comments or docstrings) names Alembic.

    Python is read by token: a name ``alembic``, the word in an f-string's
    text, or in a string that is not a docstring. Python that does not
    tokenize counts when the word is anywhere in it.
    """
    if not _WORD.search(blob):
        return False
    if not path.endswith(".py"):
        return bool(_WORD.search(live_text(path, blob)))
    try:
        for tok in tokenize.tokenize(io.BytesIO(blob).readline):
            if tok.type == tokenize.NAME and tok.string == "alembic":
                return True
            # Since 3.12 an f-string is split into parts no string check sees.
            if tok.type == _FSTRING_MIDDLE and _WORD.search(tok.string.encode("utf-8")):
                return True
    except (tokenize.TokenError, SyntaxError, ValueError):
        return True
    return bool(_WORD.search(live_text(path, blob)))


def _add_runner_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    scripts = script_files(path_set)
    if not scripts:
        return 0
    count = 0
    for path in sorted(p for p in path_set - set(scripts) if p.endswith(_CODE_EXTS)):
        text = source_bytes(path, parsed_files[path].file_info.abs_path, ctx.source_map)
        if not names_alembic(path, text):
            continue
        for target in scripts:
            if _add_edge_if_new(graph, path, target):
                graph[path][target]["hint_source"] = ALEMBIC_RUNNER_HINT
                count += 1
    return count


class _AlembicRunnerHandler:
    """Code running Alembic loads every migration script."""

    def detect(self, dctx: DetectionContext) -> bool:
        return True

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_runner_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_AlembicRunnerHandler()]
