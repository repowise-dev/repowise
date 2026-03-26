"""Git history indexer for the repowise ingestion pipeline.

Mines git history into the git_metadata table. Uses gitpython for git
operations. Parallelizes per-file git log calls with asyncio.Semaphore(20).

Non-blocking: if git is unavailable or repo has no history, log a warning
and return an empty summary. All downstream features degrade gracefully.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import structlog

logger = structlog.get_logger(__name__)

# Silence GitPython's _CatFileContentStream.__del__ ValueError spam.
# When git cat-file streams are GC'd after the subprocess pipe is closed,
# their __del__ tries to drain remaining bytes and hits a closed file.
# This is harmless but floods stderr with tracebacks.
try:
    from git.cmd import _CatFileContentStream

    _orig_del = _CatFileContentStream.__del__

    def _quiet_del(self: Any) -> None:
        try:
            _orig_del(self)
        except (ValueError, OSError):
            pass

    _CatFileContentStream.__del__ = _quiet_del  # type: ignore[assignment]
except Exception:
    pass  # git not installed — nothing to patch

# Commit message prefixes that are ALWAYS skipped (no signal value).
_HARD_SKIP_PREFIXES = ("Merge ",)

# Conventional-commit prefixes normally skipped — but kept if the message
# contains a decision-signal keyword (e.g. "build: migrate from webpack to vite").
_SOFT_SKIP_PREFIXES = ("Bump ", "chore:", "ci:", "style:", "build:", "release:")

# Lightweight subset of decision-signal keywords (mirrors decision_extractor.py).
# Used to rescue soft-skipped commits that carry architectural intent.
_DECISION_SIGNAL_WORDS: frozenset[str] = frozenset({
    "migrate", "migration", "switch to", "replace", "refactor",
    "adopt", "introduce", "deprecate", "remove", "upgrade",
    "rewrite", "extract", "convert", "transition",
})

_SKIP_AUTHORS = ("dependabot", "renovate", "github-actions")
_MIN_MESSAGE_LEN = 12

# Default per-file and co-change commit history depth.
_DEFAULT_COMMIT_LIMIT: int = 500

# Allowlist of extensions for which per-file git indexing (blame, commit
# history, hotspot/stable classification) is worth running.  Anything NOT in
# this set is skipped — data, config, markup, dotfiles, and binaries add no
# documentation value, and git blame on large JSON/YAML files is very slow.
# Co-change detection still runs across ALL tracked files regardless.
_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Python
        ".py", ".pyi",
        # JavaScript / TypeScript
        ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        # Go
        ".go",
        # Rust
        ".rs",
        # JVM
        ".java", ".kt", ".kts", ".scala",
        # C / C++
        ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx",
        # C#
        ".cs",
        # Ruby
        ".rb",
        # PHP
        ".php",
        # Swift
        ".swift",
        # Objective-C
        ".m", ".mm",
        # Elixir / Erlang
        ".ex", ".exs", ".erl", ".hrl",
        # Lua
        ".lua",
        # R
        ".r",
        # Dart
        ".dart",
        # Zig
        ".zig",
        # Julia
        ".jl",
        # Clojure
        ".clj", ".cljs", ".cljc",
        # Elm
        ".elm",
        # Haskell
        ".hs", ".lhs",
        # OCaml
        ".ml", ".mli",
        # F#
        ".fs", ".fsi", ".fsx",
        # Crystal
        ".cr",
        # Nim
        ".nim",
        # D
        ".d",
    }
)

# Files larger than this skip git blame.  blame is O(lines) and blocks the
# executor thread — for large files the commit-based ownership estimate is
# used as a fallback instead.
_MAX_BLAME_SIZE_BYTES: int = 100 * 1024  # 100 KB

# Maximum seconds to wait for a single file's git indexing.  If exceeded the
# file is recorded with whatever data was collected before the timeout and the
# semaphore slot is released so other files can proceed.
_FILE_INDEX_TIMEOUT_SECS: float = 30.0


def _should_skip_index(file_path: str) -> bool:
    """Return True for files where per-file git indexing should be skipped.

    Uses an allowlist: only files with known source-code extensions are indexed.
    Everything else (data, config, markup, dotfiles, binaries) is skipped.
    """
    return Path(file_path).suffix.lower() not in _CODE_EXTENSIONS


@dataclass
class GitIndexSummary:
    files_indexed: int
    hotspots: int
    stable_files: int
    duration_seconds: float = 0.0


class GitIndexer:
    """Mines git history into the git_metadata table.

    Uses gitpython (already a dependency) for git operations.
    Parallelizes per-file git log calls with asyncio.Semaphore(20).

    Non-blocking: if git is unavailable or repo has no history, log a warning
    and return an empty summary. All downstream features degrade gracefully.
    """

    def __init__(self, repo_path: str | Path, *, commit_limit: int | None = None) -> None:
        self.repo_path = Path(repo_path)
        self.commit_limit = commit_limit or _DEFAULT_COMMIT_LIMIT

    async def index_repo(
        self,
        repo_id: str,
        on_start: Callable[[int], None] | None = None,
        on_file_done: Callable[[], None] | None = None,
        on_commit_done: Callable[[], None] | None = None,
        on_co_change_start: Callable[[int], None] | None = None,
    ) -> tuple[GitIndexSummary, list[dict]]:
        """Full index of all tracked files. Returns summary + list of metadata dicts
        ready for bulk upsert.

        Optional progress callbacks (all thread-safe, called from the event loop
        or executor threads):
          on_start(total)    — fired once with the total number of tracked files
          on_file_done()     — fired after each file is indexed
          on_co_change_start(total) — fired once with actual commit count for
                                       co-change analysis
          on_commit_done()   — fired after each commit is processed during
                               co-change analysis
        """
        start = time.monotonic()
        repo = self._get_repo()
        if repo is None:
            return GitIndexSummary(0, 0, 0, 0.0), []

        tracked_files = self._get_tracked_files(repo)
        if not tracked_files:
            return GitIndexSummary(0, 0, 0, 0.0), []

        # Only run expensive per-file indexing (git log + blame) on code files.
        # Data/config/markup files are skipped here but still passed to
        # _compute_co_changes so co-change relationships remain complete.
        indexable_files = [fp for fp in tracked_files if not _should_skip_index(fp)]

        if on_start is not None:
            on_start(len(indexable_files))

        # Parallelized per-file indexing
        semaphore = asyncio.Semaphore(20)
        loop = asyncio.get_event_loop()

        async def index_one(file_path: str) -> dict:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, self._index_file, file_path, repo
                        ),
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Git indexing timed out for file — using partial data",
                        path=file_path,
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                    result = {"file_path": file_path}
                except Exception as exc:
                    logger.debug(
                        "Git indexing failed for file",
                        path=file_path,
                        error=str(exc),
                    )
                    result = {"file_path": file_path}
                if on_file_done is not None:
                    on_file_done()
                return result

        tasks = [index_one(fp) for fp in indexable_files]
        metadata_list = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out failures
        results: list[dict] = []
        for r in metadata_list:
            if isinstance(r, Exception):
                logger.warning("Failed to index file", error=str(r))
            else:
                results.append(r)

        # Co-change analysis uses ALL tracked files (not just indexable ones) so
        # that relationships like "whenever auth.py changes, config.yaml changes too"
        # are captured even for files we didn't run full git log/blame on.
        co_changes = await loop.run_in_executor(
            None, self._compute_co_changes, repo, set(tracked_files), self.commit_limit, 3, on_commit_done, on_co_change_start
        )
        for meta in results:
            fp = meta["file_path"]
            if fp in co_changes:
                meta["co_change_partners_json"] = json.dumps(co_changes[fp])

        # Compute percentiles
        self._compute_percentiles(results)

        duration = time.monotonic() - start
        hotspots = sum(1 for m in results if m.get("is_hotspot", False))
        stable = sum(1 for m in results if m.get("is_stable", False))

        summary = GitIndexSummary(
            files_indexed=len(results),
            hotspots=hotspots,
            stable_files=stable,
            duration_seconds=duration,
        )
        # Explicitly close the Repo to shut down the persistent git cat-file
        # process.  Without this, garbage-collected _CatFileContentStream objects
        # try to read from an already-closed pipe and spam ValueError tracebacks.
        repo.close()

        logger.info(
            "Git indexing complete",
            files=summary.files_indexed,
            hotspots=summary.hotspots,
            stable=summary.stable_files,
            duration=f"{summary.duration_seconds:.1f}s",
        )
        return summary, results

    async def index_changed_files(self, changed_file_paths: list[str]) -> list[dict]:
        """Incremental update: re-index only changed files.
        Also re-index any file whose co_change_partners include a changed file.
        """
        repo = self._get_repo()
        if repo is None:
            return []

        loop = asyncio.get_event_loop()
        semaphore = asyncio.Semaphore(20)

        async def index_one(file_path: str) -> dict:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, self._index_file, file_path, repo),
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                except (asyncio.TimeoutError, Exception) as exc:
                    logger.debug(
                        "Git indexing failed for changed file",
                        path=file_path,
                        error=str(exc),
                    )
                    return {"file_path": file_path}

        tasks = [index_one(fp) for fp in changed_file_paths]
        results_raw = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[dict] = []
        for r in results_raw:
            if isinstance(r, Exception):
                logger.warning("Failed to index changed file", error=str(r))
            else:
                results.append(r)

        repo.close()
        return results

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _get_repo(self) -> Any | None:
        try:
            import git as gitpython
            return gitpython.Repo(self.repo_path, search_parent_directories=True)
        except Exception as exc:
            logger.warning(
                "Git unavailable or not a repository",
                path=str(self.repo_path),
                error=str(exc),
            )
            return None

    def _get_tracked_files(self, repo: Any) -> list[str]:
        try:
            output = repo.git.ls_files()
            return [f for f in output.splitlines() if f.strip()]
        except Exception as exc:
            logger.warning("Failed to list tracked files", error=str(exc))
            return []

    def _index_file(self, file_path: str, repo: Any) -> dict:
        """Index a single file's git history. Runs in executor."""
        now = datetime.now(timezone.utc)
        ninety_days_ago = now - timedelta(days=90)
        thirty_days_ago = now - timedelta(days=30)
        six_months_ago = now - timedelta(days=180)

        meta: dict[str, Any] = {
            "file_path": file_path,
            "commit_count_total": 0,
            "commit_count_90d": 0,
            "commit_count_30d": 0,
            "commit_count_capped": False,
            "first_commit_at": None,
            "last_commit_at": None,
            "primary_owner_name": None,
            "primary_owner_email": None,
            "primary_owner_commit_pct": None,
            "top_authors_json": "[]",
            "significant_commits_json": "[]",
            "co_change_partners_json": "[]",
            "is_hotspot": False,
            "is_stable": False,
            "churn_percentile": 0.0,
            "age_days": 0,
        }

        try:
            commits = list(repo.iter_commits(paths=file_path, max_count=self.commit_limit))
        except Exception:
            return meta

        if not commits:
            return meta

        meta["commit_count_total"] = len(commits)
        meta["commit_count_capped"] = len(commits) >= self.commit_limit

        try:
            # Timeline
            dates = [c.committed_datetime for c in commits]
            meta["first_commit_at"] = min(dates)
            meta["last_commit_at"] = max(dates)
            meta["age_days"] = (now - min(dates)).days

            # Commit counts by time window
            for c in commits:
                cd = c.committed_datetime
                if cd.tzinfo is None:
                    cd = cd.replace(tzinfo=timezone.utc)
                if cd >= ninety_days_ago:
                    meta["commit_count_90d"] += 1
                if cd >= thirty_days_ago:
                    meta["commit_count_30d"] += 1

            # Author analysis
            author_counts: Counter[str] = Counter()
            author_emails: dict[str, str] = {}
            for c in commits:
                name = c.author.name or "unknown"
                author_counts[name] += 1
                if name not in author_emails and c.author.email:
                    author_emails[name] = c.author.email

            top_authors = []
            for name, count in author_counts.most_common(5):
                top_authors.append({
                    "name": name,
                    "email": author_emails.get(name, ""),
                    "commit_count": count,
                })
            meta["top_authors_json"] = json.dumps(top_authors)

            if top_authors:
                primary = top_authors[0]
                meta["primary_owner_name"] = primary["name"]
                meta["primary_owner_email"] = primary["email"]
                total = sum(a["commit_count"] for a in top_authors)
                meta["primary_owner_commit_pct"] = (
                    primary["commit_count"] / total if total > 0 else 0.0
                )

            # Blame ownership (overrides author-based if available).
            # Skipped for large files — git blame is O(lines) and can block the
            # executor thread for many seconds on files > 100 KB.  The commit-based
            # ownership computed above is used as a fallback.
            try:
                file_size = (self.repo_path / file_path).stat().st_size
                if file_size <= _MAX_BLAME_SIZE_BYTES:
                    blame_name, blame_email, blame_pct = self._get_blame_ownership(
                        file_path, repo
                    )
                    if blame_name:
                        meta["primary_owner_name"] = blame_name
                        meta["primary_owner_email"] = blame_email
                        meta["primary_owner_commit_pct"] = blame_pct
            except Exception:
                pass  # blame is best-effort

            # Significant commits
            sig_commits = []
            for c in commits:
                if self._is_significant_commit(c.message, c.author.name or ""):
                    sig_commits.append({
                        "sha": c.hexsha[:8],
                        "date": c.committed_datetime.isoformat(),
                        "message": c.message.strip().split("\n")[0][:200],
                        "author": c.author.name or "unknown",
                    })
                    if len(sig_commits) >= 10:
                        break
            meta["significant_commits_json"] = json.dumps(sig_commits)

            # Stable classification
            if meta["commit_count_total"] > 10 and meta["commit_count_90d"] == 0:
                meta["is_stable"] = True

        except Exception:
            pass  # return whatever partial data we have

        return meta

    def _get_blame_ownership(
        self, file_path: str, repo: Any
    ) -> tuple[str | None, str | None, float | None]:
        """Compute primary owner from git blame (who wrote the most lines)."""
        try:
            blame = repo.blame("HEAD", file_path)
        except Exception:
            return None, None, None

        line_counts: Counter[str] = Counter()
        emails: dict[str, str] = {}
        total_lines = 0

        for commit, lines in blame:
            name = commit.author.name or "unknown"
            count = len(lines)
            line_counts[name] += count
            total_lines += count
            if name not in emails and commit.author.email:
                emails[name] = commit.author.email

        if not line_counts or total_lines == 0:
            return None, None, None

        top_name = line_counts.most_common(1)[0][0]
        pct = line_counts[top_name] / total_lines
        return top_name, emails.get(top_name), pct

    def _is_significant_commit(self, message: str, author: str) -> bool:
        """Return True if the commit is considered significant.

        Filtering rules:
        1. Always skip messages shorter than _MIN_MESSAGE_LEN characters.
        2. Always skip merge commits and bot authors (no useful signal).
        3. Conventional-commit prefixes (chore:, ci:, style:, build:,
           release:, Bump) are normally skipped — UNLESS the message also
           contains a decision-signal keyword (e.g. "build: migrate from
           webpack to vite").  This rescues architecturally meaningful
           commits that happen to use a low-signal prefix.
        """
        msg = message.strip()
        if len(msg) < _MIN_MESSAGE_LEN:
            return False
        # Always skip merge commits
        for prefix in _HARD_SKIP_PREFIXES:
            if msg.startswith(prefix):
                return False
        # Always skip bot authors
        author_lower = author.lower()
        for skip in _SKIP_AUTHORS:
            if skip in author_lower:
                return False
        # Soft-skip conventional prefixes unless decision signal present
        for prefix in _SOFT_SKIP_PREFIXES:
            if msg.startswith(prefix):
                msg_lower = msg.lower()
                return any(word in msg_lower for word in _DECISION_SIGNAL_WORDS)
        return True

    def _compute_co_changes(
        self,
        repo: Any,
        all_files: set[str],
        commit_limit: int = 500,
        min_count: int = 3,
        on_commit_done: Callable[[], None] | None = None,
        on_co_change_start: Callable[[int], None] | None = None,
    ) -> dict[str, list[dict]]:
        """Walk recent commits and record co-occurrence pairs for tracked files.

        Uses a single ``git log --name-only`` call instead of spawning one
        ``git diff`` subprocess per commit — O(1) processes vs O(commit_limit).

        on_co_change_start(total) is called once with the actual number of
        commits found.  on_commit_done() is called after each commit block is
        processed.  Both are invoked from a thread-pool thread; callers must
        ensure thread safety (Rich Progress is thread-safe).
        """
        pair_counts: Counter[tuple[str, str]] = Counter()

        try:
            # One git command returns all changed-file lists.  %x00 (null byte)
            # is used as a commit separator — it cannot appear in file paths.
            raw = repo.git.log(
                f"-{commit_limit}",
                "--name-only",
                "--no-merges",
                "--format=%x00",
            )
        except Exception:
            return {}

        # Count actual commits so the caller can set an accurate progress total.
        actual_commits = raw.count("\x00")
        if on_co_change_start is not None:
            on_co_change_start(actual_commits)

        current: set[str] = set()
        for line in raw.splitlines():
            if line == "\x00" or line.startswith("\x00"):
                # Commit boundary — process the files accumulated for the
                # previous commit, then start a fresh set.
                if len(current) >= 2:
                    sorted_files = sorted(current)
                    for i in range(len(sorted_files)):
                        for j in range(i + 1, len(sorted_files)):
                            pair_counts[(sorted_files[i], sorted_files[j])] += 1
                current = set()
                if on_commit_done is not None:
                    on_commit_done()
            else:
                path = line.strip()
                if path and path in all_files:
                    current.add(path)

        # Flush the final commit's files
        if len(current) >= 2:
            sorted_files = sorted(current)
            for i in range(len(sorted_files)):
                for j in range(i + 1, len(sorted_files)):
                    pair_counts[(sorted_files[i], sorted_files[j])] += 1

        # Build result: for each file, list partners above threshold
        result: dict[str, list[dict]] = defaultdict(list)
        for (a, b), count in pair_counts.items():
            if count >= min_count:
                result[a].append({"file_path": b, "co_change_count": count})
                result[b].append({"file_path": a, "co_change_count": count})

        # Sort partners by count descending
        for fp in result:
            result[fp].sort(key=lambda x: x["co_change_count"], reverse=True)

        return dict(result)

    def _compute_percentiles(self, metadata_list: list[dict]) -> None:
        """Compute churn_percentile and is_hotspot. Mutates in place."""
        if not metadata_list:
            return

        # Sort by commit_count_90d for percentile ranking
        sorted_by_churn = sorted(
            range(len(metadata_list)),
            key=lambda i: metadata_list[i].get("commit_count_90d", 0),
        )

        total = len(metadata_list)
        for rank, idx in enumerate(sorted_by_churn):
            metadata_list[idx]["churn_percentile"] = rank / total if total > 0 else 0.0

        # Hotspot: top 25% churn (i.e., churn_percentile >= 0.75)
        for meta in metadata_list:
            commit_90d = meta.get("commit_count_90d", 0)
            churn_pct = meta.get("churn_percentile", 0.0)
            if churn_pct >= 0.75 and commit_90d > 0:
                meta["is_hotspot"] = True
