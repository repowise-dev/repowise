"""Index-free drift inputs for CI: the working tree, read in seconds.

A CI job has a checkout and no ``.repowise`` index, so this builds the
analyzer's inputs straight from the tree, reading only checkable markdown and
the command manifests.

A root ``.repowiseIgnore`` narrows which documents are scanned, as it does for
an indexed run. It does not remove paths from the tracked set: an ignored file
still exists, and a document naming it is not drift.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from repowise.core.ingestion.traverser import REPOWISE_IGNORE_FILENAME, load_extra_ignore_spec

from .analyzer import DocDriftAnalyzer
from .constants import MANIFEST_NAMES, MAX_DOC_BYTES
from .extractor import is_checkable_document
from .models import DocDriftReport

_GIT_TIMEOUT_SECONDS = 60
_GITLINK_MODE = "160000"


class LiveTreeError(RuntimeError):
    """The working tree could not be listed (git missing, or not a repository)."""


@dataclass(frozen=True)
class LiveInputs:
    """What :class:`~.analyzer.DocDriftAnalyzer` needs, read from the tree."""

    source_map: dict[str, bytes]
    tracked_paths: frozenset[str]
    root: Path
    """The repository toplevel every path above is relative to."""
    opaque_dirs: frozenset[str] = frozenset()
    """Submodule paths, whose contents the superproject cannot list."""


def _git(root: Path, *args: str) -> bytes:
    """stdout of one git call; :class:`LiveTreeError` on any failure."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        raise LiveTreeError(f"git is not available: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise LiveTreeError(f"git {args[0]} timed out in {root}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise LiveTreeError(f"{root} is not a git working tree: {detail}")
    return proc.stdout


def _toplevel(root: Path) -> Path:
    out = _git(root, "rev-parse", "--show-toplevel").decode("utf-8", errors="replace")
    return Path(out.strip())


def _list_paths(root: Path) -> frozenset[str]:
    """Paths present in the working tree, from one ``git ls-files`` call.

    Not the shared tracked-files helper: this needs ``--others --deleted -t``.
    """
    flags = ["-z", "-t", "--cached", "--deleted", "--others", "--exclude-standard"]
    out = _git(root, "ls-files", *flags)
    present: set[str] = set()
    deleted: set[str] = set()
    for entry in out.decode("utf-8", errors="replace").split("\0"):
        if not entry:
            continue
        # ``-t`` prefixes a status tag; ``R`` is a tracked file removed on disk.
        tag, _, path = entry.partition(" ")
        (deleted if tag == "R" else present).add(path)
    return frozenset(present - deleted)


def _gitlinks(root: Path) -> frozenset[str]:
    """Submodule paths (gitlink mode), initialized or not."""
    found: set[str] = set()
    for entry in _git(root, "ls-files", "-s", "-z").decode("utf-8", errors="replace").split("\0"):
        meta, _, path = entry.partition("\t")
        if path and meta.startswith(_GITLINK_MODE + " "):
            found.add(path)
    return frozenset(found)


def _read_capped(path: Path) -> bytes | None:
    try:
        if path.stat().st_size > MAX_DOC_BYTES:
            return None
        return path.read_bytes()
    except OSError:
        return None


def collect_live_inputs(root: Path) -> LiveInputs:
    """Build the analyzer's inputs from the working tree containing *root*.

    Paths are relative to the repository toplevel even when *root* is a
    subdirectory, so findings name the files CI annotates.
    """
    top = _toplevel(Path(root))
    tracked = _list_paths(top)
    ignore = load_extra_ignore_spec(top, REPOWISE_IGNORE_FILENAME)
    source_map: dict[str, bytes] = {}
    for rel in sorted(tracked):
        if rel.rsplit("/", 1)[-1] in MANIFEST_NAMES:
            wanted = True
        else:
            wanted = is_checkable_document(rel) and not ignore.match_file(rel)
        if not wanted:
            continue
        data = _read_capped(top / rel)
        if data is not None:
            source_map[rel] = data
    return LiveInputs(
        source_map=source_map,
        tracked_paths=tracked,
        root=top,
        opaque_dirs=_gitlinks(top),
    )


def run_live(root: Path, *, config: dict | None = None) -> DocDriftReport:
    """Run the drift pass over the working tree containing *root*, no index needed."""
    inputs = collect_live_inputs(Path(root))
    analyzer = DocDriftAnalyzer(
        source_map=inputs.source_map,
        tracked_paths=inputs.tracked_paths,
        repo_root=inputs.root,
        opaque_dirs=inputs.opaque_dirs,
    )
    return analyzer.analyze(config)
