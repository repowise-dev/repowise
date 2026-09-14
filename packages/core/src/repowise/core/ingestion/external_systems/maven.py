"""Parse Maven ``pom.xml`` manifests without executing Maven.

The shared local model resolves a bounded subset of parent inheritance,
properties, and dependency management.  Reactor discovery is performed by
callers that already own a pruned repository walk.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExternalSystemRecord
from .classifier import classify, display_name_for
from .io_kind import classify_io_kind
from .maven_model import load_maven_reactor

filenames: tuple[str, ...] = ("pom.xml",)
ecosystem: str = "maven"

_DEV_SCOPES = frozenset({"test", "provided", "system"})


def parse_many(
    manifest_paths: list[Path] | tuple[Path, ...],
    repo_root: Path,
) -> list[ExternalSystemRecord]:
    """Return effective direct dependencies for several POMs in one model pass.

    Local parents are read when referenced from inside ``repo_root``. Only
    requested manifests contribute records; parents loaded solely for
    inheritance remain implementation context.
    """

    requested = {path.resolve() for path in manifest_paths}
    reactor = load_maven_reactor(repo_root, tuple(requested))

    records: list[ExternalSystemRecord] = []
    seen: set[tuple[str, str]] = set()
    for project in reactor.projects:
        if (repo_root / project.manifest).resolve() not in requested:
            continue
        for dependency in project.dependencies:
            key = (dependency.coordinate, dependency.declared_in)
            if dependency.has_unresolved_coordinate or key in seen:
                continue
            seen.add(key)
            records.append(
                ExternalSystemRecord(
                    name=dependency.coordinate,
                    ecosystem=ecosystem,
                    declared_in=dependency.declared_in,
                    version=dependency.version,
                    display_name=display_name_for(dependency.artifact_id),
                    category=classify(dependency.artifact_id),
                    io_kind=classify_io_kind(dependency.artifact_id),
                    is_dev_dep=dependency.scope in _DEV_SCOPES,
                    extras={
                        "scope": dependency.scope,
                        "optional": str(dependency.optional).lower(),
                        "type": dependency.dependency_type,
                    },
                )
            )
    return records


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    """Return effective direct dependencies declared by one POM."""
    return parse_many([manifest_path], repo_root)
