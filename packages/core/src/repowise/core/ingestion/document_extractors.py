"""Plugin seam for extracting Markdown from otherwise unsupported files."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

from repowise.core.registry.plugins import SeamRegistry

from .models import FileInfo

#: Hard ceiling on a claimed document's source bytes. The parent reads the
#: whole file into memory and pickles it to a spawned worker, so a declared
#: ``max_source_bytes`` above this is clamped to it.
MAX_DOCUMENT_SOURCE_BYTES: int = 100 * 1024 * 1024  # 100 MiB


class DocumentExtractor(Protocol):
    """Convert a claimed source file into Markdown for the existing parser.

    An implementation may also set ``max_source_bytes: int`` to raise or lower
    the source size ceiling for the files it claims. Without it, a claimed file
    keeps the size limit of any file with no parser.
    """

    extensions: frozenset[str]
    version: str

    def extract(self, path: Path, data: bytes) -> str: ...


def _validate_extension(name: str, extension: object) -> None:
    if not isinstance(extension, str):
        raise ValueError(f"document extractor {name!r} has invalid extension {extension!r}")
    if "/" in extension or "\\" in extension:
        raise ValueError(f"document extractor {name!r} has invalid extension {extension!r}")
    if (
        not extension.startswith(".")
        or extension.count(".") != 1
        or extension != extension.lower()
        or len(extension) < 2
    ):
        raise ValueError(f"document extractor {name!r} has invalid extension {extension!r}")


def _validate_document_extractor(name: str, extractor: DocumentExtractor) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("document extractor name must be a non-empty string")
    extensions = getattr(extractor, "extensions", None)
    if not isinstance(extensions, (set, frozenset)) or not extensions:
        raise ValueError(f"document extractor {name!r} must claim a non-empty set of extensions")
    for extension in extensions:
        _validate_extension(name, extension)
    version = getattr(extractor, "version", None)
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"document extractor {name!r} must provide a non-empty string version")
    if not callable(getattr(extractor, "extract", None)):
        raise ValueError(f"document extractor {name!r} must provide extract(path, data)")
    _validate_max_source_bytes(name, extractor)


def _validate_max_source_bytes(name: str, extractor: DocumentExtractor) -> None:
    if not hasattr(extractor, "max_source_bytes"):
        return
    limit = extractor.max_source_bytes
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError(
            f"document extractor {name!r} has invalid max_source_bytes {limit!r}; "
            "expected a positive int"
        )


class DocumentExtractorRegistry(SeamRegistry[DocumentExtractor]):
    """A seam registry which rejects invalid extractor registrations immediately."""

    def __new__(cls, seam: str) -> DocumentExtractorRegistry:
        registry = super().__new__(cls, seam)
        if not isinstance(registry, cls):
            for name, extractor in registry._impls.items():
                _validate_document_extractor(name, extractor)
            # Preserve references if generic seam discovery created the singleton first.
            registry.__class__ = cls
        return registry

    def register(self, name: str, impl: DocumentExtractor, *, default: bool = False) -> None:
        _validate_document_extractor(name, impl)
        super().register(name, impl, default=default)


document_extractors: DocumentExtractorRegistry = DocumentExtractorRegistry("document-extractors")


class DocumentExtractionError(RuntimeError):
    """An extractor failed for a source path."""


def _extension_claims() -> dict[str, tuple[str, DocumentExtractor]]:
    claims: dict[str, tuple[str, DocumentExtractor]] = {}
    for name, extractor in document_extractors.all():
        for extension in extractor.extensions:
            claims[extension] = (name, extractor)
    return claims


def document_extractor_claims() -> dict[str, str]:
    """Map each unsupported extension to the implementation that will handle it."""
    from .traverser import _language_from_name_or_ext

    return {
        extension: name
        for extension, (name, _impl) in _extension_claims().items()
        if _language_from_name_or_ext(Path(f"source{extension}")) is None
    }


def document_extractor_for_path(
    path: Path | str, *, language: str | None = None
) -> tuple[str, DocumentExtractor] | None:
    """Return the later-registered extractor for an unsupported extension."""
    path = Path(path)
    from .traverser import _language_from_name_or_ext

    if language not in (None, "unknown"):
        return None
    if _language_from_name_or_ext(path) is not None:
        return None
    return _extension_claims().get(path.suffix.lower())


def document_source_ceiling(path: Path | str, default: int) -> int:
    """Source byte ceiling for *path*: its extractor's declared one, else *default*.

    The one rule every eligibility check uses (traversal, historical blobs, MCP
    reads). Decided from the extension alone, so it costs no I/O. An unclaimed
    path gets *default* unchanged.
    """
    claim = document_extractor_for_path(path)
    if claim is None:
        return default
    declared = getattr(claim[1], "max_source_bytes", None)
    return min(default if declared is None else declared, MAX_DOCUMENT_SOURCE_BYTES)


def is_extracted_document(
    path: Path | str,
    source: bytes | None = None,
    *,
    claims: dict[str, str] | None = None,
) -> bool:
    """Whether *path* is routed through a document extractor, not a code parser."""
    from .traverser import _detect_language

    path = Path(path)
    owners = document_extractor_claims() if claims is None else claims
    if path.suffix.lower() not in owners:
        return False
    if source is None:
        with path.open("rb") as stream:
            source = stream.read(131072)
    language = _detect_language(path, source)
    return language == "unknown"


def source_fingerprint(file_info: FileInfo, source: bytes) -> str:
    """Hash original bytes and extractor identity/version without extracting."""
    name = file_info.extractor_name
    version = file_info.extractor_version
    if name is None or version is None:
        return hashlib.sha256(source).hexdigest()
    return document_fingerprint(name, version, source)


def document_fingerprint(name: str, version: str, source: bytes) -> str:
    """Identity of source bytes under a particular extractor implementation."""
    digest = hashlib.sha256()
    digest.update(b"repowise-document-extractor\0")
    digest.update(name.encode("utf-8"))
    digest.update(b"\0")
    digest.update(version.encode("utf-8"))
    digest.update(b"\0")
    digest.update(source)
    return digest.hexdigest()


def _extract_worker(task: tuple[str, str, str, bytes]) -> bytes:
    """Spawn-pool worker; plugins are rediscovered in the spawned process."""
    name, version, path_text, source = task
    path = Path(path_text)
    try:
        extractor = dict(document_extractors.all())[name]
    except KeyError as exc:
        raise DocumentExtractionError(
            f"document extractor {name!r} is not discoverable in the spawned worker"
        ) from exc
    if extractor.version != version:
        raise DocumentExtractionError(
            f"document extractor {name!r} version changed from {version!r} "
            f"to {extractor.version!r} in the spawned worker for {path}"
        )
    return _extract(name, extractor, path, source)


def extract_documents_bounded(
    requests: list[tuple[str, str, Path, str, bytes]],
) -> list[bytes]:
    """Extract eligible sources in the spawn pool; pool failures never run inline."""
    if not requests:
        return []
    from repowise.core.pipeline.parse_pool import (
        PENDING,
        parse_timeout_s,
        run_with_deadline,
    )
    from repowise.core.pipeline.phases.ingestion import parse_pool_workers

    results: list[object] = [PENDING] * len(requests)
    try:
        run_with_deadline(
            _extract_worker,
            [
                (name, version, str(path), source)
                for name, version, path, _display_path, source in requests
            ],
            parse_pool_workers(len(requests)),
            results,
            timeout=parse_timeout_s(),
        )
    except Exception as exc:
        name, _version, _path, display_path, _source = requests[0]
        raise DocumentExtractionError(
            f"document extractor {name!r} could not run for {display_path}: {exc}"
        ) from exc
    extracted: list[bytes] = []
    for (name, _version, _path, display_path, _source), result in zip(
        requests, results, strict=True
    ):
        if isinstance(result, BaseException):
            raise DocumentExtractionError(
                f"document extractor {name!r} failed for {display_path}: {result}"
            ) from result
        if result is PENDING:
            raise DocumentExtractionError(
                f"document extractor {name!r} did not complete for {display_path}"
            )
        if not isinstance(result, bytes):
            raise DocumentExtractionError(
                f"document extractor {name!r} returned invalid output for {display_path}"
            )
        extracted.append(result)
    return extracted


def _extract(
    name: str,
    extractor: DocumentExtractor,
    extractor_path: Path,
    source: bytes,
) -> bytes:
    try:
        markdown = extractor.extract(extractor_path, source)
        if not isinstance(markdown, str):
            raise TypeError("extract() must return Markdown as str")
        return markdown.encode("utf-8")
    except Exception as exc:
        raise DocumentExtractionError(
            f"document extractor {name!r} failed for {extractor_path}: {exc}"
        ) from exc
