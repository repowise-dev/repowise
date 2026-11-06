"""Remember how each file's imports resolved, so a patch can skip re-resolving them.

Resolving an import writes its result back onto the parsed file
(``Import.resolved_file``, ``NamedBinding.source_file``), and later passes read
it: the JVM type-reference pass builds its candidate files from it. The parse
cache deliberately stores a file *before* the build mutates it, so a patched
update, which re-resolves only the changed files, would hand those passes
unresolved imports for every other file and pick different targets than a full
build.

This side cache records the resolved values after each build, keyed like the
parse cache by path and content hash, and puts them back on the files a patch
does not re-resolve. Its entries are only trusted for a file whose content hash
still matches; anything else sends the update to the full rebuild.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

_FILENAME = "resolved_imports.pkl"
_VERSION = 1


def _snapshot(parsed: Any) -> tuple[str, list[tuple[str | None, tuple[str | None, ...]]]]:
    return (
        parsed.content_hash,
        [
            (imp.resolved_file, tuple(b.source_file for b in imp.bindings))
            for imp in parsed.imports
        ],
    )


def save_resolutions(index_dir: Path, parsed_files: Iterable[Any]) -> None:
    """Write the resolved imports of every built file. Best effort."""
    from repowise.core.ingestion.parse_cache import parser_fingerprint

    try:
        payload = {
            "version": _VERSION,
            "fingerprint": parser_fingerprint(),
            "files": {pf.file_info.path: _snapshot(pf) for pf in parsed_files},
        }
        dump_sealed_pickle(Path(index_dir) / _FILENAME, payload, domain=_FILENAME)
    except Exception as exc:
        log.debug("resolved_imports_save_failed", error=str(exc))


def load_resolutions(index_dir: Path) -> dict[str, tuple] | None:
    """The saved snapshot, or ``None`` when absent, stale or unverifiable."""
    from repowise.core.ingestion.parse_cache import parser_fingerprint

    try:
        payload = load_sealed_pickle(Path(index_dir) / _FILENAME, domain=_FILENAME)
    except Exception:
        return None
    if payload.get("version") != _VERSION or payload.get("fingerprint") != parser_fingerprint():
        return None
    return payload.get("files")


def covers(saved: dict[str, tuple] | None, parsed_files: Iterable[Any], skip: Collection[str]) -> bool:
    """Whether *saved* holds a matching snapshot for every file not in *skip*."""
    if saved is None:
        return False
    for pf in parsed_files:
        path = pf.file_info.path
        if path in skip:
            continue
        entry = saved.get(path)
        if entry is None or entry[0] != pf.content_hash or len(entry[1]) != len(pf.imports):
            return False
        if any(len(bindings) != len(imp.bindings) for imp, (_, bindings) in zip(pf.imports, entry[1], strict=True)):
            return False
    return True


def restore_resolutions(saved: dict[str, tuple], parsed_files: Iterable[Any], skip: Collection[str]) -> None:
    """Put the saved resolved values back on every file not in *skip*."""
    for pf in parsed_files:
        if pf.file_info.path in skip:
            continue
        for imp, (resolved, sources) in zip(pf.imports, saved[pf.file_info.path][1], strict=True):
            imp.resolved_file = resolved
            for binding, source in zip(imp.bindings, sources, strict=True):
                binding.source_file = source
