"""Common types for manifest parsers.

Each ecosystem-specific parser (npm, pypi, cargo, go, nuget) implements the
``ManifestParser`` protocol below and returns a list of
``ExternalSystemRecord`` instances. The orchestrator stitches them together
and persists them via ``crud.upsert_external_systems``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ExternalSystemRecord:
    """Plain-data dependency record. Mirrors the ExternalSystem ORM row but
    is kept dialect-free so parsers don't depend on the persistence layer.
    """

    name: str
    ecosystem: str
    declared_in: str  # repo-relative path to the manifest file
    version: str | None = None
    display_name: str = ""
    category: str = "library"  # populated by classifier
    io_kind: str | None = None  # populated by io_kind classifier; None = untyped
    is_dev_dep: bool = False
    extras: dict[str, str] = field(default_factory=dict)


class ManifestParser(Protocol):
    """Stateless parser for one manifest format.

    Implementations should be pure functions of (manifest_path, repo_root)
    and never raise on malformed input — return an empty list instead and
    let the caller log a warning. Ingestion must not crash because one
    repo ships a broken pyproject.toml.
    """

    #: Filenames this parser handles, e.g., ("package.json",).
    filenames: tuple[str, ...]

    ecosystem: str

    def parse(self, manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
        ...
