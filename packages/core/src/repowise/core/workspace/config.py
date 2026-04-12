"""Workspace configuration — dataclass and YAML persistence.

Pure data module with no CLI or DB dependencies. Handles the
``.repowise-workspace.yaml`` file at the workspace root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WORKSPACE_CONFIG_FILENAME = ".repowise-workspace.yaml"
WORKSPACE_DATA_DIR = ".repowise-workspace"
CURRENT_VERSION = 1


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class RepoEntry:
    """One repository within a workspace."""

    path: str  # Relative to workspace root, POSIX-style forward slashes
    alias: str  # Unique short name
    is_primary: bool = False
    indexed_at: str | None = None  # ISO 8601 timestamp of last index
    last_commit_at_index: str | None = None  # Git SHA at last index

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"path": self.path, "alias": self.alias}
        if self.is_primary:
            d["is_primary"] = True
        if self.indexed_at is not None:
            d["indexed_at"] = self.indexed_at
        if self.last_commit_at_index is not None:
            d["last_commit_at_index"] = self.last_commit_at_index
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepoEntry:
        return cls(
            path=str(data["path"]),
            alias=str(data["alias"]),
            is_primary=bool(data.get("is_primary", False)),
            indexed_at=data.get("indexed_at"),
            last_commit_at_index=data.get("last_commit_at_index"),
        )


@dataclass
class WorkspaceConfig:
    """Workspace-level configuration stored in ``.repowise-workspace.yaml``."""

    version: int = CURRENT_VERSION
    repos: list[RepoEntry] = field(default_factory=list)
    default_repo: str | None = None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict suitable for ``yaml.dump``."""
        return {
            "version": self.version,
            "default_repo": self.default_repo,
            "repos": [r.to_dict() for r in self.repos],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceConfig:
        """Construct from a parsed YAML dict."""
        version = int(data.get("version", CURRENT_VERSION))
        default_repo = data.get("default_repo")

        repos: list[RepoEntry] = []
        for entry in data.get("repos", []):
            repos.append(RepoEntry.from_dict(entry))

        return cls(
            version=version,
            repos=repos,
            default_repo=str(default_repo) if default_repo else None,
        )

    def save(self, workspace_root: Path) -> Path:
        """Write config to ``workspace_root / .repowise-workspace.yaml``.

        Returns the path to the written file.
        """
        config_path = workspace_root / WORKSPACE_CONFIG_FILENAME
        content = yaml.dump(
            self.to_dict(),
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        config_path.write_text(content, encoding="utf-8")
        return config_path

    @classmethod
    def load(cls, workspace_root: Path) -> WorkspaceConfig:
        """Load from ``workspace_root / .repowise-workspace.yaml``.

        Raises :class:`FileNotFoundError` if the config file does not exist.
        """
        config_path = workspace_root / WORKSPACE_CONFIG_FILENAME
        if not config_path.is_file():
            raise FileNotFoundError(f"Workspace config not found: {config_path}")
        text = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(text) or {}
        return cls.from_dict(data)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_repo(self, alias: str) -> RepoEntry | None:
        """Find a repo by alias. Returns ``None`` if not found."""
        for repo in self.repos:
            if repo.alias == alias:
                return repo
        return None

    def get_primary(self) -> RepoEntry | None:
        """Return the primary/default repo entry, or ``None``."""
        if self.default_repo:
            found = self.get_repo(self.default_repo)
            if found:
                return found
        # Fallback: first repo marked as primary
        for repo in self.repos:
            if repo.is_primary:
                return repo
        # Fallback: first repo
        return self.repos[0] if self.repos else None

    def repo_paths(self, workspace_root: Path) -> list[Path]:
        """Return absolute resolved paths for all repos."""
        root = Path(workspace_root).resolve()
        return [(root / entry.path).resolve() for entry in self.repos]

    def repo_aliases(self) -> list[str]:
        """Return all repo aliases in order."""
        return [r.alias for r in self.repos]

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_repo(self, entry: RepoEntry) -> None:
        """Add a repo entry. Raises ``ValueError`` if alias already exists."""
        if self.get_repo(entry.alias) is not None:
            raise ValueError(f"Repo alias already exists: {entry.alias}")
        self.repos.append(entry)

    def remove_repo(self, alias: str) -> RepoEntry | None:
        """Remove a repo by alias. Returns the removed entry or ``None``."""
        for i, repo in enumerate(self.repos):
            if repo.alias == alias:
                removed = self.repos.pop(i)
                if self.default_repo == alias:
                    self.default_repo = self.repos[0].alias if self.repos else None
                return removed
        return None


# ---------------------------------------------------------------------------
# Workspace root detection
# ---------------------------------------------------------------------------


def find_workspace_root(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default: cwd) looking for ``.repowise-workspace.yaml``.

    Returns the directory containing the file, or ``None`` if not found.
    Stops at the filesystem root.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        if (current / WORKSPACE_CONFIG_FILENAME).is_file():
            return current
        parent = current.parent
        if parent == current:
            return None  # reached filesystem root
        current = parent


def ensure_workspace_data_dir(workspace_root: Path) -> Path:
    """Create the ``.repowise-workspace/`` data directory if it doesn't exist.

    This directory holds workspace-level artifacts (overlay graph, cross-repo
    analysis results). Distinct from the config file.
    """
    data_dir = workspace_root / WORKSPACE_DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir
