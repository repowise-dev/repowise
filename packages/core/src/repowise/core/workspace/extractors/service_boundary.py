"""Service boundary detection for monorepo sub-services.

Walks a repo directory tree looking for marker files (package.json, go.mod,
Dockerfile, etc.) that indicate independent service boundaries. Used to
distinguish intra-service calls from inter-service calls when matching
contracts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVICE_MARKERS = frozenset({
    "package.json",
    "go.mod",
    "Dockerfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Cargo.toml",
    "pyproject.toml",
    "requirements.txt",
    "mix.exs",
})

_BLOCKED_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", "target", "vendor", ".next", ".nuxt",
    ".tox", ".mypy_cache", ".gradle", ".mvn", "out", "bin",
})

_SOURCE_EXTENSIONS = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".go",
    ".rs", ".rb", ".php", ".cs", ".kt", ".scala", ".ex", ".exs",
})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ServiceBoundary:
    """A detected service boundary within a repo."""

    service_path: str  # Relative to repo root, POSIX-style
    service_name: str  # Basename of the directory
    markers: list[str] = field(default_factory=list)
    confidence: float = 0.75

    def to_dict(self) -> dict:
        return {
            "service_path": self.service_path,
            "service_name": self.service_name,
            "markers": self.markers,
            "confidence": self.confidence,
        }


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_service_boundaries(repo_path: Path) -> list[ServiceBoundary]:
    """Walk *repo_path* and find directories that look like service roots.

    A directory is a service boundary if it contains at least one marker file
    AND at least one source file (to avoid flagging config-only dirs).
    The repo root itself is excluded — it's the default boundary.

    Confidence scoring:
      - 1 marker  → 0.75
      - 2 markers → 0.90
      - 3+ markers → 1.00
    """
    repo_root = repo_path.resolve()
    boundaries: list[ServiceBoundary] = []

    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Prune blocked dirs in-place
        dirnames[:] = [
            d for d in dirnames
            if d not in _BLOCKED_DIRS and not d.startswith(".")
        ]

        current = Path(dirpath)
        if current == repo_root:
            continue  # skip repo root itself

        # Check for marker files
        found_markers = [f for f in filenames if f in _SERVICE_MARKERS]
        if not found_markers:
            continue

        # Require at least one source file in this dir (not recursively)
        has_source = any(
            Path(f).suffix.lower() in _SOURCE_EXTENSIONS
            for f in filenames
        )
        if not has_source:
            # Check immediate subdirs for source files
            try:
                has_source = any(
                    Path(dirpath, d, f).suffix.lower() in _SOURCE_EXTENSIONS
                    for d in dirnames
                    for f in os.listdir(Path(dirpath, d))
                    if os.path.isfile(Path(dirpath, d, f))
                ) if dirnames else False
            except (PermissionError, OSError):
                has_source = False

        if not has_source:
            continue

        rel_path = current.relative_to(repo_root).as_posix()
        marker_count = len(found_markers)

        if marker_count >= 3:
            confidence = 1.0
        elif marker_count == 2:
            confidence = 0.9
        else:
            confidence = 0.75

        boundaries.append(ServiceBoundary(
            service_path=rel_path,
            service_name=current.name,
            markers=sorted(found_markers),
            confidence=confidence,
        ))

    return boundaries


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def assign_service(
    file_path: str,
    boundaries: list[ServiceBoundary],
) -> str | None:
    """Return the service_path of the deepest boundary that is a prefix of *file_path*.

    Uses longest-prefix matching so that ``services/auth/handler.py`` matches
    ``services/auth`` over ``services``.
    """
    normalized = file_path.replace("\\", "/")
    best: ServiceBoundary | None = None
    best_length = 0

    for boundary in boundaries:
        prefix = boundary.service_path + "/"
        if normalized.startswith(prefix) and len(boundary.service_path) > best_length:
            best = boundary
            best_length = len(boundary.service_path)

    return best.service_path if best else None
