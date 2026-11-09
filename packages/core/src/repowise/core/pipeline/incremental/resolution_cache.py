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

It also records every file each import resolved to, which a patch that adds or
removes files compares against to find the unchanged files it re-routes, and
the field types each file's classes declare, which another file's chained
calls read from its source (``obj.field.method()``): an edit that changes them
reaches every file that spells the field.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

import structlog

from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

log = structlog.get_logger(__name__)

_FILENAME = "resolved_imports.pkl"
_VERSION = 3

_Entry = tuple[str | None, tuple[str | None, ...], tuple[str, ...]]


FieldTypes = dict[str, dict[str, str | None]]


def field_types(parsed: Any, source: bytes | None) -> FieldTypes | None:
    """The field types *parsed*'s classes declare, or ``None`` without its source."""
    from repowise.core.ingestion.call_receiver_typing import class_field_types

    if source is None:
        return None
    # Decoded the way the resolver reads a file (``Path.read_text``).
    text = source.decode("utf-8", errors="ignore").replace("\r\n", "\n").replace("\r", "\n")
    return class_field_types(parsed, text)


def _snapshot(
    parsed: Any, targets: list[tuple[str, ...]], fields: FieldTypes | None
) -> tuple[str, list[_Entry], FieldTypes | None]:
    return (
        parsed.content_hash,
        [
            (imp.resolved_file, tuple(b.source_file for b in imp.bindings), tuple(found))
            for imp, found in zip(parsed.imports, targets, strict=True)
        ],
        fields,
    )


def _fields_for(
    parsed: Any, source_map: dict[str, bytes], previous: dict[str, tuple] | None
) -> FieldTypes | None:
    """Field types for one file, reused from *previous* when its content is unchanged."""
    entry = (previous or {}).get(parsed.file_info.path)
    if entry is not None and entry[0] == parsed.content_hash and entry[2] is not None:
        return entry[2]
    return field_types(parsed, source_map.get(parsed.file_info.path))


def save_resolutions(
    index_dir: Path,
    parsed_files: Iterable[Any],
    import_targets: dict[str, list[tuple[str, ...]]],
    source_map: dict[str, bytes] | None = None,
    config_inputs: dict[str, Any] | None = None,
) -> None:
    """Write the resolved imports and class field types of every built file. Best effort.

    *config_inputs* is what the build read from its manifests
    (:func:`~.config_inputs.record_inputs`), saved with the rest so the two
    always describe the same build.

    *import_targets* is the build's ``{path: targets of each import}``; a
    file missing from it is left out, so the next patch falls back for it.
    A file whose source is not in *source_map* is saved with no field record,
    which the next patch also treats as unknown.
    """
    from repowise.core.ingestion.parse_cache import parser_fingerprint

    try:
        previous = load_resolutions(Path(index_dir))
        files = {}
        for pf in parsed_files:
            targets = import_targets.get(pf.file_info.path)
            if targets is not None and len(targets) == len(pf.imports):
                fields = _fields_for(pf, source_map or {}, previous)
                files[pf.file_info.path] = _snapshot(pf, targets, fields)
        payload = {
            "version": _VERSION,
            "fingerprint": parser_fingerprint(),
            "files": files,
            "config_inputs": config_inputs,
        }
        dump_sealed_pickle(Path(index_dir) / _FILENAME, payload, domain=_FILENAME)
    except Exception as exc:
        log.debug("resolved_imports_save_failed", error=str(exc))


def _load_payload(index_dir: Path) -> dict[str, Any] | None:
    from repowise.core.ingestion.parse_cache import parser_fingerprint

    try:
        payload = load_sealed_pickle(Path(index_dir) / _FILENAME, domain=_FILENAME)
    except Exception:
        return None
    if payload.get("version") != _VERSION or payload.get("fingerprint") != parser_fingerprint():
        return None
    return payload


def load_resolutions(index_dir: Path) -> dict[str, tuple] | None:
    """The saved snapshot, or ``None`` when absent, stale or unverifiable."""
    payload = _load_payload(index_dir)
    return None if payload is None else payload.get("files")


def load_config_inputs(index_dir: Path) -> dict[str, Any] | None:
    """What the last build read from its manifests, or ``None`` when unrecorded."""
    payload = _load_payload(index_dir)
    return None if payload is None else payload.get("config_inputs")


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
        if any(len(bindings) != len(imp.bindings) for imp, (_, bindings, _t) in zip(pf.imports, entry[1], strict=True)):
            return False
    return True


def restore_resolutions(
    saved: dict[str, tuple],
    parsed_files: Iterable[Any],
    skip: Collection[str],
    import_targets: dict[str, list[tuple[str, ...]]] | None = None,
) -> None:
    """Put the saved resolved values back on every file not in *skip*.

    *import_targets*, when given, gets each restored file's import targets.
    """
    for pf in parsed_files:
        path = pf.file_info.path
        if path in skip:
            continue
        entries = saved[path][1]
        for imp, (resolved, sources, _targets) in zip(pf.imports, entries, strict=True):
            imp.resolved_file = resolved
            for binding, source in zip(imp.bindings, sources, strict=True):
                binding.source_file = source
        if import_targets is not None:
            import_targets[path] = [targets for _r, _s, targets in entries]
