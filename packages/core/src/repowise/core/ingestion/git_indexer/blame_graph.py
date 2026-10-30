"""Changed-path Bloom filters for the per-file pass, kept out of the user's ``.git``.

``git blame`` and ``git log -- <path>`` walk every commit and diff its tree
to learn whether the path changed. A commit-graph written with
``--changed-paths`` answers that from a Bloom filter instead, which makes a
blame several times faster on a long history. Git reads these filters only
from a commit-graph, so the graph goes in a scratch object directory: the
per-file workers run with that directory as their object store and the
repository's own objects as an alternate. Nothing in the repository is
written, and the output is identical; the filters only let git skip work.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.git_cli import run_git

logger = structlog.get_logger(__name__)

__all__ = ["BlameGraph"]

_OBJECT_DIR_ENV = "GIT_OBJECT_DIRECTORY"
_ALTERNATES_ENV = "GIT_ALTERNATE_OBJECT_DIRECTORIES"

# A graph that takes longer than this is abandoned and the pass runs as before.
_WRITE_TIMEOUT_SECS = 600


class BlameGraph:
    """A scratch commit-graph for one index run; ``env`` is ``None`` when unavailable.

    :meth:`target` reads the repository (on the thread that owns it),
    :meth:`write` builds the graph (on any thread); hand ``env`` to every git
    call that walks a path's history, then :meth:`close` to delete the scratch
    directory.
    """

    def __init__(self, env: dict[str, str] | None = None, scratch: str | None = None) -> None:
        self.env = env
        self._scratch = scratch

    @staticmethod
    def target(repo: Any) -> tuple[str, Path] | None:
        """``(working_dir, objects_dir)`` for :meth:`write`, or ``None`` to skip.

        Skipped when the caller already points git at another object
        directory, and on shallow clones, where git ignores commit-graphs.
        """
        if os.environ.get(_OBJECT_DIR_ENV):
            return None
        try:
            if repo.git.rev_parse("--is-shallow-repository").strip() == "true":
                return None
            return str(repo.working_dir), Path(repo.common_dir) / "objects"
        except Exception:
            return None

    @classmethod
    def write(cls, working_dir: str, objects: Path) -> BlameGraph:
        """Write the graph for every commit reachable from a ref; best effort."""
        scratch = tempfile.mkdtemp(prefix="repowise-graph-")
        alternates = [str(objects)]
        if os.environ.get(_ALTERNATES_ENV):
            alternates.append(os.environ[_ALTERNATES_ENV])
        env = {_OBJECT_DIR_ENV: scratch, _ALTERNATES_ENV: os.pathsep.join(alternates)}
        try:
            proc = run_git(
                ["commit-graph", "write", "--reachable", "--changed-paths"],
                working_dir,
                env={**os.environ, **env},
                timeout=_WRITE_TIMEOUT_SECS,
            )
            error = (proc.stderr.strip() or f"exit {proc.returncode}") if proc.returncode else ""
        except (OSError, subprocess.TimeoutExpired) as exc:
            error = str(exc)
        if error:
            logger.debug("blame_graph_skipped", error=error)
            _remove(scratch)
            return cls()
        return cls(env, scratch)

    def close(self) -> None:
        """Delete the scratch directory once the per-file pass is over."""
        if self._scratch:
            _remove(self._scratch)
            self._scratch = None


def _remove(scratch: str) -> None:
    """Delete *scratch*; git writes the graph read-only, which Windows will not unlink."""
    with contextlib.suppress(OSError):
        os.chmod(Path(scratch, "info", "commit-graph"), stat.S_IWRITE)
    shutil.rmtree(scratch, ignore_errors=True)
