"""Cross-repo intelligence — co-change detection, manifest scanning, overlay persistence.

Runs during ``repowise update --workspace`` (write path).  The resulting JSON
is loaded by :class:`CrossRepoEnricher` in the MCP server (read path).

No new DB tables — all cross-repo data lives in
``.repowise-workspace/cross_repo_edges.json``.
"""

from __future__ import annotations

import json
import logging
import math
import subprocess
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import WorkspaceConfig, ensure_workspace_data_dir

_log = logging.getLogger("repowise.workspace.cross_repo")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CROSS_REPO_EDGES_FILENAME = "cross_repo_edges.json"

# Same decay constant as intra-repo co-change (git_indexer.py)
_CO_CHANGE_DECAY_TAU: float = 180.0

_DEFAULT_TIME_WINDOW_HOURS: int = 24
_DEFAULT_COMMIT_LIMIT: int = 500
_MIN_CROSS_REPO_SCORE: float = 1.0
_MAX_EDGES: int = 200


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CrossRepoCoChange:
    source_repo: str
    source_file: str
    target_repo: str
    target_file: str
    strength: float  # decay-weighted co-change score
    frequency: int  # raw count of temporally-correlated commit pairs
    last_date: str  # ISO date of most recent co-change


@dataclass
class CrossRepoPackageDep:
    source_repo: str
    target_repo: str
    source_manifest: str
    kind: str  # npm_local_path, pip_path, cargo_path, go_replace


@dataclass
class CrossRepoOverlay:
    version: int = 1
    generated_at: str = ""
    co_changes: list[CrossRepoCoChange] = field(default_factory=list)
    package_deps: list[CrossRepoPackageDep] = field(default_factory=list)
    repo_summaries: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "co_changes": [asdict(c) for c in self.co_changes],
            "package_deps": [asdict(d) for d in self.package_deps],
            "repo_summaries": self.repo_summaries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CrossRepoOverlay:
        return cls(
            version=data.get("version", 1),
            generated_at=data.get("generated_at", ""),
            co_changes=[
                CrossRepoCoChange(**c) for c in data.get("co_changes", [])
            ],
            package_deps=[
                CrossRepoPackageDep(**d) for d in data.get("package_deps", [])
            ],
            repo_summaries=data.get("repo_summaries", {}),
        )


# ---------------------------------------------------------------------------
# Git log mining
# ---------------------------------------------------------------------------


@dataclass
class _GitCommit:
    author_email: str
    timestamp: int  # Unix epoch
    files: list[str] = field(default_factory=list)


def _parse_git_log(
    repo_path: Path,
    commit_limit: int = _DEFAULT_COMMIT_LIMIT,
) -> list[_GitCommit]:
    """Run ``git log`` and parse into structured commit records.

    Returns list of commits with author email, timestamp, and changed files.
    Uses subprocess.run — same pattern as ``update.py:get_head_commit()``.
    """
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"-{commit_limit}",
                "--format=%x00%ae|%ct",
                "--name-only",
                "--no-merges",
            ],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=60,
            encoding="utf-8",
        )
        if result.returncode != 0:
            _log.debug("git log failed for %s: %s", repo_path, result.stderr)
            return []
    except Exception:
        _log.debug("git log subprocess failed for %s", repo_path, exc_info=True)
        return []

    commits: list[_GitCommit] = []
    current: _GitCommit | None = None

    for line in result.stdout.splitlines():
        if line.startswith("\x00") or line == "\x00":
            # Commit boundary — flush previous, parse header
            if current is not None and current.files:
                commits.append(current)

            header = line.lstrip("\x00").strip()
            parts = header.split("|", 1)
            if len(parts) == 2:
                email = parts[0].strip()
                try:
                    ts = int(parts[1].strip())
                except (ValueError, TypeError):
                    ts = 0
                current = _GitCommit(author_email=email, timestamp=ts)
            else:
                current = None
        elif current is not None:
            path = line.strip()
            if path:
                current.files.append(path)

    # Flush last commit
    if current is not None and current.files:
        commits.append(current)

    return commits


# ---------------------------------------------------------------------------
# Cross-repo co-change detection
# ---------------------------------------------------------------------------


def detect_cross_repo_co_changes(
    repo_paths: dict[str, Path],
    *,
    time_window_hours: int = _DEFAULT_TIME_WINDOW_HOURS,
    commit_limit: int = _DEFAULT_COMMIT_LIMIT,
    min_score: float = _MIN_CROSS_REPO_SCORE,
) -> list[CrossRepoCoChange]:
    """Find files across repos committed by same author within *time_window*.

    Algorithm:
    1. Parse git logs for all repos
    2. Group commits by author email
    3. For same author, find commit pairs from different repos within window
    4. All files from correlated commits are cross-repo co-change partners
    5. Apply temporal decay (same as intra-repo)
    6. Filter by min_score, cap at MAX_EDGES
    """
    if len(repo_paths) < 2:
        return []

    now_ts = time.time()
    window_seconds = time_window_hours * 3600

    # Step 1: Parse git logs for all repos
    repo_commits: dict[str, list[_GitCommit]] = {}
    for alias, path in repo_paths.items():
        commits = _parse_git_log(path, commit_limit)
        if commits:
            repo_commits[alias] = commits

    if len(repo_commits) < 2:
        return []

    # Step 2: Group all commits by author, tagged with repo alias
    author_commits: dict[str, list[tuple[str, _GitCommit]]] = defaultdict(list)
    for alias, commits in repo_commits.items():
        for c in commits:
            author_commits[c.author_email].append((alias, c))

    # Step 3: For each author, find temporally-correlated cross-repo pairs
    # Accumulate (src_repo, src_file, tgt_repo, tgt_file) -> (score, frequency, last_ts)
    pair_scores: dict[tuple[str, str, str, str], float] = defaultdict(float)
    pair_freq: dict[tuple[str, str, str, str], int] = defaultdict(int)
    pair_last_ts: dict[tuple[str, str, str, str], int] = {}

    for author, tagged_commits in author_commits.items():
        if len(tagged_commits) < 2:
            continue

        # Sort by timestamp
        tagged_commits.sort(key=lambda x: x[1].timestamp)

        # Sliding window: for each commit, look ahead for commits from
        # different repos within the time window
        for i in range(len(tagged_commits)):
            repo_a, commit_a = tagged_commits[i]
            for j in range(i + 1, len(tagged_commits)):
                repo_b, commit_b = tagged_commits[j]

                time_diff = commit_b.timestamp - commit_a.timestamp
                if time_diff > window_seconds:
                    break  # Past window — no more matches for commit_a

                if repo_a == repo_b:
                    continue  # Same repo — skip

                # Cross-repo match found
                age_days = max((now_ts - commit_b.timestamp) / 86400.0, 0.0)
                weight = math.exp(-age_days / _CO_CHANGE_DECAY_TAU)

                # Create file pairs (limit to avoid O(N*M) explosion on huge commits)
                files_a = commit_a.files[:20]
                files_b = commit_b.files[:20]

                for fa in files_a:
                    for fb in files_b:
                        # Normalize key ordering to avoid symmetric duplicates
                        if (repo_a, fa) > (repo_b, fb):
                            key = (repo_b, fb, repo_a, fa)
                        else:
                            key = (repo_a, fa, repo_b, fb)
                        pair_scores[key] += weight
                        pair_freq[key] += 1
                        ts = max(commit_a.timestamp, commit_b.timestamp)
                        if key not in pair_last_ts or ts > pair_last_ts[key]:
                            pair_last_ts[key] = ts

    # Step 4: Filter and build results
    results: list[CrossRepoCoChange] = []
    for (src_repo, src_file, tgt_repo, tgt_file), score in pair_scores.items():
        if score < min_score:
            continue
        freq = pair_freq[(src_repo, src_file, tgt_repo, tgt_file)]
        last_ts = pair_last_ts.get((src_repo, src_file, tgt_repo, tgt_file), 0)
        last_date = (
            datetime.fromtimestamp(last_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            if last_ts > 0
            else ""
        )
        results.append(CrossRepoCoChange(
            source_repo=src_repo,
            source_file=src_file,
            target_repo=tgt_repo,
            target_file=tgt_file,
            strength=round(score, 2),
            frequency=freq,
            last_date=last_date,
        ))

    # Sort by strength descending and cap
    results.sort(key=lambda x: -x.strength)
    return results[:_MAX_EDGES]


# ---------------------------------------------------------------------------
# Package / manifest dependency detection
# ---------------------------------------------------------------------------


def _resolve_target_repo(
    relative_ref: str,
    source_repo_path: Path,
    repo_paths: dict[str, Path],
) -> str | None:
    """Resolve a relative path reference to a repo alias, or None."""
    try:
        target_abs = (source_repo_path / relative_ref).resolve()
        for alias, repo_path in repo_paths.items():
            if target_abs == repo_path.resolve() or str(target_abs).startswith(
                str(repo_path.resolve())
            ):
                return alias
    except Exception:
        pass
    return None


def _scan_package_json(
    repo_path: Path,
    repo_paths: dict[str, Path],
    alias: str,
) -> list[CrossRepoPackageDep]:
    """Scan package.json for local file: references to sibling repos."""
    results: list[CrossRepoPackageDep] = []
    pkg_json = repo_path / "package.json"
    if not pkg_json.is_file():
        return results

    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except Exception:
        return results

    for dep_key in ("dependencies", "devDependencies", "peerDependencies"):
        deps = data.get(dep_key, {})
        if not isinstance(deps, dict):
            continue
        for _name, version in deps.items():
            if isinstance(version, str) and version.startswith("file:"):
                rel_path = version[5:]  # strip "file:"
                target = _resolve_target_repo(rel_path, repo_path, repo_paths)
                if target and target != alias:
                    results.append(CrossRepoPackageDep(
                        source_repo=alias,
                        target_repo=target,
                        source_manifest="package.json",
                        kind="npm_local_path",
                    ))

    # Check workspaces field
    workspaces = data.get("workspaces", [])
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages", [])
    # Workspaces are globs — we just check if they point to sibling repos
    for ws in workspaces if isinstance(workspaces, list) else []:
        if isinstance(ws, str) and ".." in ws:
            target = _resolve_target_repo(ws.rstrip("/*"), repo_path, repo_paths)
            if target and target != alias:
                results.append(CrossRepoPackageDep(
                    source_repo=alias,
                    target_repo=target,
                    source_manifest="package.json",
                    kind="npm_workspace",
                ))

    return results


def _scan_pyproject_toml(
    repo_path: Path,
    repo_paths: dict[str, Path],
    alias: str,
) -> list[CrossRepoPackageDep]:
    """Scan pyproject.toml for path dependencies."""
    results: list[CrossRepoPackageDep] = []
    toml_path = repo_path / "pyproject.toml"
    if not toml_path.is_file():
        return results

    try:
        # Use tomllib (Python 3.11+) or tomli
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return results

    # Poetry path dependencies
    poetry_deps = (
        data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    )
    for _name, spec in poetry_deps.items() if isinstance(poetry_deps, dict) else []:
        if isinstance(spec, dict) and "path" in spec:
            target = _resolve_target_repo(spec["path"], repo_path, repo_paths)
            if target and target != alias:
                results.append(CrossRepoPackageDep(
                    source_repo=alias,
                    target_repo=target,
                    source_manifest="pyproject.toml",
                    kind="pip_path",
                ))

    # PEP 621 dependencies with path (uncommon but possible via tool configs)
    for group_key in ("dependencies", "optional-dependencies"):
        group = data.get("project", {}).get(group_key, {})
        if isinstance(group, dict):
            for _name, spec in group.items():
                if isinstance(spec, dict) and "path" in spec:
                    target = _resolve_target_repo(spec["path"], repo_path, repo_paths)
                    if target and target != alias:
                        results.append(CrossRepoPackageDep(
                            source_repo=alias,
                            target_repo=target,
                            source_manifest="pyproject.toml",
                            kind="pip_path",
                        ))

    return results


def _scan_cargo_toml(
    repo_path: Path,
    repo_paths: dict[str, Path],
    alias: str,
) -> list[CrossRepoPackageDep]:
    """Scan Cargo.toml for path dependencies."""
    results: list[CrossRepoPackageDep] = []
    cargo_path = repo_path / "Cargo.toml"
    if not cargo_path.is_file():
        return results

    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        with open(cargo_path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return results

    for section in ("dependencies", "dev-dependencies", "build-dependencies"):
        deps = data.get(section, {})
        for _name, spec in deps.items():
            if isinstance(spec, dict) and "path" in spec:
                target = _resolve_target_repo(spec["path"], repo_path, repo_paths)
                if target and target != alias:
                    results.append(CrossRepoPackageDep(
                        source_repo=alias,
                        target_repo=target,
                        source_manifest="Cargo.toml",
                        kind="cargo_path",
                    ))

    return results


def _scan_go_mod(
    repo_path: Path,
    repo_paths: dict[str, Path],
    alias: str,
) -> list[CrossRepoPackageDep]:
    """Scan go.mod for replace directives pointing to sibling repos."""
    results: list[CrossRepoPackageDep] = []
    go_mod = repo_path / "go.mod"
    if not go_mod.is_file():
        return results

    try:
        content = go_mod.read_text(encoding="utf-8")
    except Exception:
        return results

    # Parse "replace" directives: `replace foo => ../sibling`
    in_replace_block = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("replace ("):
            in_replace_block = True
            continue
        if in_replace_block and stripped == ")":
            in_replace_block = False
            continue

        # Single-line replace or inside block
        if stripped.startswith("replace ") or in_replace_block:
            parts = stripped.replace("replace ", "").split("=>")
            if len(parts) == 2:
                target_path = parts[1].strip().split()[0]  # first token after =>
                if target_path.startswith("..") or target_path.startswith("./"):
                    target = _resolve_target_repo(target_path, repo_path, repo_paths)
                    if target and target != alias:
                        results.append(CrossRepoPackageDep(
                            source_repo=alias,
                            target_repo=target,
                            source_manifest="go.mod",
                            kind="go_replace",
                        ))

    return results


def detect_package_dependencies(
    repo_paths: dict[str, Path],
) -> list[CrossRepoPackageDep]:
    """Scan all repos for manifest-based cross-repo dependencies."""
    results: list[CrossRepoPackageDep] = []
    seen: set[tuple[str, str, str]] = set()  # (source, target, kind)

    for alias, path in repo_paths.items():
        for scanner in (_scan_package_json, _scan_pyproject_toml, _scan_cargo_toml, _scan_go_mod):
            for dep in scanner(path, repo_paths, alias):
                key = (dep.source_repo, dep.target_repo, dep.kind)
                if key not in seen:
                    seen.add(key)
                    results.append(dep)

    return results


# ---------------------------------------------------------------------------
# Repo summaries
# ---------------------------------------------------------------------------


def _build_repo_summaries(
    repo_paths: dict[str, Path],
    co_changes: list[CrossRepoCoChange],
    package_deps: list[CrossRepoPackageDep],
) -> dict[str, dict]:
    """Build per-repo summary stats."""
    summaries: dict[str, dict] = {}

    # Count cross-repo edges per repo
    edge_counts: dict[str, int] = defaultdict(int)
    for cc in co_changes:
        edge_counts[cc.source_repo] += 1
        edge_counts[cc.target_repo] += 1
    for pd in package_deps:
        edge_counts[pd.source_repo] += 1
        edge_counts[pd.target_repo] += 1

    for alias in repo_paths:
        summaries[alias] = {
            "cross_repo_edge_count": edge_counts.get(alias, 0),
        }

    return summaries


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_overlay(overlay: CrossRepoOverlay, workspace_root: Path) -> Path:
    """Save overlay to ``.repowise-workspace/cross_repo_edges.json``."""
    data_dir = ensure_workspace_data_dir(workspace_root)
    out_path = data_dir / CROSS_REPO_EDGES_FILENAME
    out_path.write_text(
        json.dumps(overlay.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


def load_overlay(workspace_root: Path) -> CrossRepoOverlay | None:
    """Load overlay from ``.repowise-workspace/cross_repo_edges.json``.

    Returns ``None`` if the file doesn't exist or is corrupt.
    """
    from .config import WORKSPACE_DATA_DIR

    path = workspace_root / WORKSPACE_DATA_DIR / CROSS_REPO_EDGES_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return CrossRepoOverlay.from_dict(data)
    except Exception:
        _log.warning("Failed to load cross-repo overlay from %s", path, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_cross_repo_analysis(
    ws_config: WorkspaceConfig,
    workspace_root: Path,
    changed_repos: list[str],
) -> CrossRepoOverlay:
    """Full cross-repo analysis pipeline.

    Called from :func:`run_cross_repo_hooks` after workspace update.
    """
    # Build repo_paths dict
    repo_paths: dict[str, Path] = {}
    for entry in ws_config.repos:
        abs_path = (workspace_root / entry.path).resolve()
        if abs_path.is_dir():
            repo_paths[entry.alias] = abs_path

    if len(repo_paths) < 2:
        _log.debug("Skipping cross-repo analysis — fewer than 2 repos")
        return CrossRepoOverlay()

    _log.info(
        "Running cross-repo analysis across %d repos (changed: %s)",
        len(repo_paths),
        ", ".join(changed_repos),
    )

    # Co-change detection (CPU-bound git subprocess calls)
    import asyncio

    co_changes = await asyncio.to_thread(
        detect_cross_repo_co_changes, repo_paths
    )

    # Package dependency detection (file I/O)
    package_deps = await asyncio.to_thread(
        detect_package_dependencies, repo_paths
    )

    # Build summaries
    repo_summaries = _build_repo_summaries(repo_paths, co_changes, package_deps)

    overlay = CrossRepoOverlay(
        version=1,
        generated_at=datetime.now(timezone.utc).isoformat(),
        co_changes=co_changes,
        package_deps=package_deps,
        repo_summaries=repo_summaries,
    )

    # Persist
    out_path = save_overlay(overlay, workspace_root)
    _log.info(
        "Cross-repo analysis complete: %d co-change edges, %d package deps → %s",
        len(co_changes),
        len(package_deps),
        out_path,
    )

    return overlay
