"""Shared source reading and cached document-to-Markdown conversion."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from .document_extractors import (
    document_fingerprint,
    extract_documents_bounded,
    is_extracted_document,
)
from .traverser import _DEFAULT_MAX_FILE_SIZE_BYTES

_log = logging.getLogger(__name__)


def read_document_sources(
    repo_path: Path,
    requests: list[tuple[str, str, Path, str, bytes]],
    *,
    write_cache: bool = True,
) -> list[bytes]:
    """Read cached Markdown or batch-convert already eligible working-tree/Git bytes.

    Source-size checks belong before this boundary. Extracted output is expected
    to be smaller than its source and has no separate size ceiling.
    """
    from repowise.core.cache_seal import dump_sealed_pickle, load_sealed_pickle

    outputs: dict[int, bytes] = {}
    missing = []
    for index, (name, version, path, display_path, source) in enumerate(requests):
        try:
            relative = path.relative_to(repo_path).as_posix()
        except ValueError:
            relative = path.relative_to(repo_path.resolve()).as_posix()
        identity = f"{relative}\0{document_fingerprint(name, version, source)}"
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        cache_path = repo_path.resolve() / ".repowise" / "document-cache" / key
        domain = str(cache_path.absolute())
        try:
            cached = load_sealed_pickle(cache_path, domain=domain)
        except Exception as exc:
            _log.debug("Document cache miss for %s: %s", display_path, exc)
            cached = None
        if isinstance(cached, bytes):
            outputs[index] = cached
        else:
            missing.append((index, cache_path, domain))

    converted = extract_documents_bounded(
        [requests[index] for index, _path, _domain in missing],
    )
    for (index, cache_path, domain), markdown in zip(missing, converted, strict=True):
        outputs[index] = markdown
        if not write_cache:
            continue
        try:
            dump_sealed_pickle(cache_path, markdown, domain=domain)
        except Exception as exc:
            _log.debug("Could not cache document %s: %s", requests[index][3], exc)
    return [outputs[index] for index in range(len(requests))]


def read_source(
    repo_path: Path,
    file_path: str,
    *,
    allow_extracted: bool = True,
) -> bytes | None:
    """Read within a repo using the indexed path for routing, never the symlink target.

    Optional serving callers may catch DocumentExtractionError; indexing must
    propagate it. Extraction-capable async callers must offload this reader.
    """
    from .document_extractors import document_extractor_for_path
    from .traverser import _detect_language

    indexed_path = repo_path / file_path
    try:
        resolved = indexed_path.resolve()
        resolved.relative_to(repo_path.resolve())
        if not is_extracted_document(indexed_path):
            return resolved.read_bytes()
        if not allow_extracted:
            return None
        with resolved.open("rb") as stream:
            source = stream.read(_DEFAULT_MAX_FILE_SIZE_BYTES + 1)
        if len(source) > _DEFAULT_MAX_FILE_SIZE_BYTES:
            return None
    except (OSError, ValueError):
        return None
    claim = document_extractor_for_path(
        indexed_path, language=_detect_language(indexed_path, source)
    )
    if claim is None:
        return source
    name, extractor = claim
    return read_document_sources(
        repo_path,
        [(name, extractor.version, indexed_path, file_path, source)],
    )[0]
