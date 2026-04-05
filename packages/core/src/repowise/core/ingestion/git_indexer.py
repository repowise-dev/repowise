"""Git history indexer for the repowise ingestion pipeline.

Mines git history into the git_metadata table. Uses gitpython for git
operations. Parallelizes per-file git log calls with asyncio.Semaphore(20).

Non-blocking: if git is unavailable or repo has no history, log a warning
and return an empty summary. All downstream features degrade gracefully.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

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
        with contextlib.suppress(ValueError, OSError):
            _orig_del(self)

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
_DECISION_SIGNAL_WORDS: frozenset[str] = frozenset(
    {
        "migrate",
        "migration",
        "switch to",
        "replace",
        "refactor",
        "adopt",
        "introduce",
        "deprecate",
        "remove",
        "upgrade",
        "rewrite",
        "extract",
        "convert",
        "transition",
    }
)

_SKIP_AUTHORS = ("dependabot", "renovate", "github-actions")
_MIN_MESSAGE_LEN = 12

# Default per-file and co-change commit history depth.
_DEFAULT_COMMIT_LIMIT: int = 500

# Commit message classification regexes (Phase 2.2).
_COMMIT_CATEGORIES: dict[str, re.Pattern[str]] = {
    "feature": re.compile(
        r"\b(add|implement|introduce|create|new|feat)\b",
        re.IGNORECASE,
    ),
    "refactor": re.compile(
        r"\b(refactor|restructure|cleanup|clean.up|rename|reorganize|extract|simplify|move)\b",
        re.IGNORECASE,
    ),
    "fix": re.compile(
        r"\b(fix|bug|patch|hotfix|revert|regression|broken|crash|error)\b",
        re.IGNORECASE,
    ),
    "dependency": re.compile(
        r"\b(upgrade|bump|update.dep|migrate.to|switch.to|dependency|dependencies)\b",
        re.IGNORECASE,
    ),
}

# Co-change temporal decay: half-life ~125 days (lambda for exp(-t/tau)).
_CO_CHANGE_DECAY_TAU: float = 180.0

# Regex to extract PR/MR numbers from commit messages.
# Matches: "#123", "Merge pull request #456", "(#789)", "!42" (GitLab MR)
_PR_NUMBER_RE = re.compile(r"(?:pull request |)\#(\d+)|\(#(\d+)\)|!(\d+)")

# Allowlist of extensions for which per-file git indexing (blame, commit
# history, hotspot/stable classification) is worth running.  Anything NOT in
# this set is skipped — data, config, markup, dotfiles, and binaries add no
# documentation value, and git blame on large JSON/YAML files is very slow.
# Co-change detection still runs across ALL tracked files regardless.
_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        # Python
        ".py",
        ".pyi",
        # JavaScript / TypeScript
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        # Go
        ".go",
        # Rust
        ".rs",
        # JVM
        ".java",
        ".kt",
        ".kts",
        ".scala",
        # C / C++
        ".c",
        ".h",
        ".cpp",
        ".cc",
        ".cxx",
        ".hpp",
        ".hxx",
        # C#
        ".cs",
        # Ruby
        ".rb",
        # PHP
        ".php",
        # Swift
        ".swift",
        # Objective-C
        ".m",
        ".mm",
        # Elixir / Erlang
        ".ex",
        ".exs",
        ".erl",
        ".hrl",
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
        ".clj",
        ".cljs",
        ".cljc",
        # Elm
        ".elm",
        # Haskell
        ".hs",
        ".lhs",
        # OCaml
        ".ml",
        ".mli",
        # F#
        ".fs",
        ".fsi",
        ".fsx",
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
_FILE_INDEX_TIMEOUT_SECS: float = 45.0


@dataclass
class _CommitRec:
    """Lightweight commit record parsed from ``git log --numstat``."""

    sha: str
    author_name: str
    author_email: str
    ts: int  # unix epoch
    is_merge: bool
    subject: str
    added: int = 0
    deleted: int = 0


_RENAME_RE = re.compile(r"\{(.+?) => (.+?)\}")


def _extract_rename_paths(stat_path: str, known_paths: set[str]) -> tuple[str | None, str | None]:
    """Extract old/new paths from a git numstat rename line and add to *known_paths*.

    Git ``--numstat`` with ``--follow`` emits rename lines like::

        10\t5\t{old => new}/shared_suffix
        10\t5\told_dir/{old_name => new_name}.py

    This helper parses both forms, adds both expanded paths to *known_paths*,
    and returns ``(old_path, new_path)`` so the caller can attribute churn to
    the correct file.  Returns ``(None, None)`` if the pattern is not found.
    """
    m = _RENAME_RE.search(stat_path)
    if m:
        prefix = stat_path[: m.start()]
        suffix = stat_path[m.end() :]
        old_path = prefix + m.group(1) + suffix
        new_path = prefix + m.group(2) + suffix
        known_paths.add(old_path)
        known_paths.add(new_path)
        return old_path, new_path
    return None, None


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

    def __init__(
        self,
        repo_path: str | Path,
        *,
        commit_limit: int | None = None,
        follow_renames: bool = False,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.commit_limit = commit_limit or _DEFAULT_COMMIT_LIMIT
        self.follow_renames = follow_renames

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

        # Use a dedicated executor so timed-out threads don't block
        # asyncio.run() cleanup (the default executor waits for ALL threads).
        from concurrent.futures import ThreadPoolExecutor

        executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="git-idx")
        semaphore = asyncio.Semaphore(20)
        loop = asyncio.get_event_loop()

        def _index_one_sync(file_path: str) -> dict:
            """Use a per-thread Repo to avoid shared-handle issues on Windows."""
            try:
                import git as gitpython

                thread_repo = gitpython.Repo(
                    self.repo_path,
                    search_parent_directories=True,
                )
                try:
                    return self._index_file(file_path, thread_repo)
                finally:
                    thread_repo.close()
            except Exception:
                return {"file_path": file_path}

        async def index_one(file_path: str) -> dict:
            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(executor, _index_one_sync, file_path),
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                except TimeoutError:
                    logger.debug(
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

        # Run per-file indexing and co-change analysis in parallel — they are
        # independent (co-change only needs tracked_files, not per-file results).
        file_tasks = [index_one(fp) for fp in indexable_files]

        async def _co_change_task() -> dict[str, list[dict]]:
            return await loop.run_in_executor(
                executor,
                self._compute_co_changes,
                repo,
                set(tracked_files),
                self.commit_limit,
                3,
                on_commit_done,
                on_co_change_start,
            )

        metadata_list, co_changes = await asyncio.gather(
            asyncio.gather(*file_tasks, return_exceptions=True),
            _co_change_task(),
        )

        # Abandon any timed-out threads immediately instead of letting
        # asyncio.run() block for minutes during default-executor cleanup.
        executor.shutdown(wait=False, cancel_futures=True)

        # Filter out failures
        results: list[dict] = []
        for r in metadata_list:
            if isinstance(r, Exception):
                logger.warning("Failed to index file", error=str(r))
            else:
                results.append(r)

        # Merge co-change partners into per-file metadata
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

        def _index_one_sync(file_path: str) -> dict:
            """Use a per-thread Repo to avoid shared-handle issues on Windows."""
            try:
                import git as gitpython

                thread_repo = gitpython.Repo(
                    self.repo_path,
                    search_parent_directories=True,
                )
                try:
                    return self._index_file(file_path, thread_repo)
                finally:
                    thread_repo.close()
            except Exception:
                return {"file_path": file_path}

        async def index_one(file_path: str) -> dict:
            async with semaphore:
                try:
                    return await asyncio.wait_for(
                        loop.run_in_executor(None, _index_one_sync, file_path),
                        timeout=_FILE_INDEX_TIMEOUT_SECS,
                    )
                except (TimeoutError, Exception) as exc:
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
        """Index a single file's git history. Runs in executor.

        Uses a single ``git log --numstat`` call to collect commit metadata,
        line-churn stats, and merge-commit flag in one subprocess instead of
        the previous three separate calls (_get_commits, _get_line_stats,
        _get_merge_commit_count).
        """
        now = datetime.now(UTC)
        ninety_days_ago = now - timedelta(days=90)
        thirty_days_ago = now - timedelta(days=30)
        ninety_days_ago_ts = ninety_days_ago.timestamp()
        thirty_days_ago_ts = thirty_days_ago.timestamp()

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
            "commit_categories_json": "{}",
            "is_hotspot": False,
            "is_stable": False,
            "churn_percentile": 0.0,
            "age_days": 0,
            # Phase 2 fields
            "lines_added_90d": 0,
            "lines_deleted_90d": 0,
            "avg_commit_size": 0.0,
            "recent_owner_name": None,
            "recent_owner_commit_pct": None,
            "bus_factor": 0,
            "contributor_count": 0,
            # Phase 3 fields
            "original_path": None,
            "merge_commit_count_90d": 0,
        }

        try:
            # Single git log call: header line + optional numstat lines per commit.
            # Format: NUL-delimited record per commit so we can split reliably.
            # Fields: sha, author name, author email, unix timestamp, parent SHAs, subject
            log_args: list[str] = []
            if self.follow_renames:
                log_args.append("--follow")
            log_args += [
                f"-{self.commit_limit}",
                "--numstat",
                "--format=%x00%H%x1f%an%x1f%ae%x1f%ct%x1f%P%x1f%s",
                "--",
                file_path,
            ]
            raw = repo.git.log(*log_args)
        except Exception:
            return meta

        if not raw.strip():
            return meta

        # Parse records — each starts with a \x00 marker line followed by
        # zero-or-more numstat lines (added\tdeleted\tpath).
        # When --follow is active, older commits may reference previous file
        # names so we track all names seen via rename markers ("{old => new}").
        known_paths: set[str] = {file_path}
        # When --follow is active, seed known_paths with the original path
        # so that numstat lines referencing the old name (without a rename
        # marker in the log window) are still counted.
        orig_path: str | None = None
        if self.follow_renames:
            orig_path = self._detect_original_path(file_path, repo)
            if orig_path:
                known_paths.add(orig_path)
        commits: list[_CommitRec] = []
        current: _CommitRec | None = None

        for line in raw.splitlines():
            if line.startswith("\x00"):
                parts = line.lstrip("\x00").split("\x1f")
                if len(parts) >= 6:
                    sha, an, ae, ct, parents, subj = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                    try:
                        ts = int(ct)
                    except ValueError:
                        ts = 0
                    current = _CommitRec(
                        sha=sha,
                        author_name=an or "unknown",
                        author_email=ae,
                        ts=ts,
                        is_merge=len(parents.split()) > 1,
                        subject=subj,
                    )
                    commits.append(current)
            elif current is not None and line.strip():
                # numstat line: added\tdeleted\tpath — only count the target file.
                # With --follow, git may emit rename lines like
                # "10\t5\t{old_dir => new_dir}/file.py" or just the old path.
                numstat_parts = line.split("\t")
                if len(numstat_parts) >= 3:
                    stat_path = numstat_parts[2]
                    # Track renamed paths so we can match them in older commits.
                    # For rename lines the raw stat_path (with {old => new}) won't
                    # match known_paths directly — check the expanded new path instead.
                    match_path = stat_path
                    if "=>" in stat_path:
                        _old, _new = _extract_rename_paths(stat_path, known_paths)
                        match_path = _new or stat_path
                    if match_path in known_paths or match_path == file_path:
                        try:
                            current.added += int(numstat_parts[0]) if numstat_parts[0] != "-" else 0
                            current.deleted += int(numstat_parts[1]) if numstat_parts[1] != "-" else 0
                        except ValueError:
                            pass

        if not commits:
            return meta

        meta["commit_count_total"] = len(commits)
        meta["commit_count_capped"] = len(commits) >= self.commit_limit

        try:
            timestamps = [c.ts for c in commits if c.ts > 0]
            if timestamps:
                first_ts = min(timestamps)
                last_ts = max(timestamps)
                meta["first_commit_at"] = datetime.fromtimestamp(first_ts, tz=UTC)
                meta["last_commit_at"] = datetime.fromtimestamp(last_ts, tz=UTC)
                meta["age_days"] = (now - datetime.fromtimestamp(first_ts, tz=UTC)).days

            author_counts: Counter[str] = Counter()
            author_emails: dict[str, str] = {}
            recent_author_counts: Counter[str] = Counter()

            for c in commits:
                is_recent_90 = c.ts >= ninety_days_ago_ts
                is_recent_30 = c.ts >= thirty_days_ago_ts
                if is_recent_90:
                    meta["commit_count_90d"] += 1
                    recent_author_counts[c.author_name] += 1
                    meta["lines_added_90d"] += c.added
                    meta["lines_deleted_90d"] += c.deleted
                    if c.is_merge:
                        meta["merge_commit_count_90d"] += 1
                if is_recent_30:
                    meta["commit_count_30d"] += 1
                author_counts[c.author_name] += 1
                if c.author_name not in author_emails and c.author_email:
                    author_emails[c.author_name] = c.author_email

            c90 = meta["commit_count_90d"]
            total_churn = meta["lines_added_90d"] + meta["lines_deleted_90d"]
            meta["avg_commit_size"] = total_churn / c90 if c90 > 0 else 0.0

            # Contributor count & bus factor
            meta["contributor_count"] = len(author_counts)
            total_commits = sum(author_counts.values())
            if total_commits > 0:
                threshold = total_commits * 0.8
                running = 0
                bus = 0
                for _name, cnt in author_counts.most_common():
                    running += cnt
                    bus += 1
                    if running >= threshold:
                        break
                meta["bus_factor"] = bus

            # Top authors
            top_authors = []
            for name, count in author_counts.most_common(5):
                top_authors.append(
                    {
                        "name": name,
                        "email": author_emails.get(name, ""),
                        "commit_count": count,
                    }
                )
            meta["top_authors_json"] = json.dumps(top_authors)

            if top_authors:
                primary = top_authors[0]
                meta["primary_owner_name"] = primary["name"]
                meta["primary_owner_email"] = primary["email"]
                meta["primary_owner_commit_pct"] = (
                    primary["commit_count"] / total_commits if total_commits > 0 else 0.0
                )

            # Recent owner (90d)
            if recent_author_counts:
                recent_top = recent_author_counts.most_common(1)[0]
                meta["recent_owner_name"] = recent_top[0]
                recent_total = sum(recent_author_counts.values())
                meta["recent_owner_commit_pct"] = (
                    recent_top[1] / recent_total if recent_total > 0 else 0.0
                )

            # Blame ownership (overrides author-based if available).
            # Skipped for large files — git blame is O(lines) and can block the
            # executor thread for many seconds on files > 100 KB.
            try:
                file_size = (self.repo_path / file_path).stat().st_size
                if file_size <= _MAX_BLAME_SIZE_BYTES:
                    blame_name, blame_email, blame_pct = self._get_blame_ownership(file_path, repo)
                    if blame_name:
                        meta["primary_owner_name"] = blame_name
                        meta["primary_owner_email"] = blame_email
                        meta["primary_owner_commit_pct"] = blame_pct
            except Exception:
                pass  # blame is best-effort

            # Significant commits + classification + PR extraction
            sig_commits: list[dict[str, Any]] = []
            sig_full = False
            category_counts: Counter[str] = Counter()
            for c in commits:
                msg = c.subject[:200]
                if not sig_full and self._is_significant_commit(msg, c.author_name):
                    entry: dict[str, Any] = {
                        "sha": c.sha[:8],
                        "date": datetime.fromtimestamp(c.ts, tz=UTC).isoformat() if c.ts else "",
                        "message": msg,
                        "author": c.author_name,
                    }
                    pr_match = _PR_NUMBER_RE.search(msg)
                    if pr_match:
                        pr_num = pr_match.group(1) or pr_match.group(2) or pr_match.group(3)
                        entry["pr_number"] = int(pr_num)
                    sig_commits.append(entry)
                    if len(sig_commits) >= 10:
                        sig_full = True
                # Classify ALL commits for accurate category ratios
                for cat, pattern in _COMMIT_CATEGORIES.items():
                    if pattern.search(msg):
                        category_counts[cat] += 1
                        break

            meta["significant_commits_json"] = json.dumps(sig_commits)
            meta["commit_categories_json"] = json.dumps(dict(category_counts))

            # Original path detection (rename tracking) — reuse the result
            # from the known_paths seeding above to avoid a duplicate subprocess.
            if self.follow_renames and orig_path:
                meta["original_path"] = orig_path

            # Stable classification
            if meta["commit_count_total"] > 10 and meta["commit_count_90d"] == 0:
                meta["is_stable"] = True

        except Exception:
            logger.debug("git_indexer_partial_failure", file_path=file_path, exc_info=True)

        return meta

    def _detect_original_path(self, file_path: str, repo: Any) -> str | None:
        """If --follow reveals the file was renamed, return its earliest prior path."""
        try:
            raw = repo.git.log(
                "--follow",
                f"-{self.commit_limit}",
                "--format=",
                "--name-only",
                "--",
                file_path,
            )
        except Exception:
            return None

        # Paths appear newest-first; the last distinct path is the original.
        prev_path: str | None = None
        for line in raw.splitlines():
            p = line.strip()
            if p and p != file_path:
                prev_path = p  # keep overwriting — last one is oldest
        return prev_path

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

        Applies exponential temporal decay so recent co-changes weigh more
        than ancient ones.  The ``%ct`` format captures commit timestamps.

        on_co_change_start(total) is called once with the actual number of
        commits found.  on_commit_done() is called after each commit block is
        processed.  Both are invoked from a thread-pool thread; callers must
        ensure thread safety (Rich Progress is thread-safe).
        """
        pair_scores: defaultdict[tuple[str, str], float] = defaultdict(float)
        pair_last_date: dict[tuple[str, str], int] = {}  # pair → latest Unix ts
        now_ts = time.time()

        try:
            # %x00 = commit separator, %ct = committer timestamp (Unix epoch).
            raw = repo.git.log(
                f"-{commit_limit}",
                "--name-only",
                "--no-merges",
                "--format=%x00%ct",
            )
        except Exception:
            return {}

        # Count actual commits so the caller can set an accurate progress total.
        actual_commits = raw.count("\x00")
        if on_co_change_start is not None:
            on_co_change_start(actual_commits)

        current: set[str] = set()
        current_ts: int = 0

        def _flush_commit() -> None:
            if len(current) < 2:
                return
            age_days = max((now_ts - current_ts) / 86400.0, 0.0)
            weight = math.exp(-age_days / _CO_CHANGE_DECAY_TAU)
            sorted_files = sorted(current)
            for i in range(len(sorted_files)):
                for j in range(i + 1, len(sorted_files)):
                    pair = (sorted_files[i], sorted_files[j])
                    pair_scores[pair] += weight
                    if pair not in pair_last_date or current_ts > pair_last_date[pair]:
                        pair_last_date[pair] = current_ts

        for line in raw.splitlines():
            if line == "\x00" or line.startswith("\x00"):
                # Commit boundary — flush previous, parse timestamp.
                _flush_commit()
                current = set()
                # Extract Unix timestamp after the \x00 marker
                ts_part = line.lstrip("\x00").strip()
                try:
                    current_ts = int(ts_part)
                except (ValueError, TypeError):
                    current_ts = 0
                if on_commit_done is not None:
                    on_commit_done()
            else:
                path = line.strip()
                if path and path in all_files:
                    current.add(path)

        _flush_commit()  # final commit

        # Build result: for each file, list partners above threshold.
        # min_count is compared against raw weight (decay-adjusted).
        # A score of 3.0 roughly equals 3 recent co-changes or many older ones.
        result: dict[str, list[dict]] = defaultdict(list)
        for (a, b), score in pair_scores.items():
            if score >= min_count:
                last_ts = pair_last_date.get((a, b), 0)
                last_date = (
                    datetime.fromtimestamp(last_ts, tz=UTC).strftime("%Y-%m-%d")
                    if last_ts > 0
                    else None
                )
                entry_a = {
                    "file_path": b,
                    "co_change_count": round(score, 2),
                    "last_co_change": last_date,
                }
                entry_b = {
                    "file_path": a,
                    "co_change_count": round(score, 2),
                    "last_co_change": last_date,
                }
                result[a].append(entry_a)
                result[b].append(entry_b)

        # Sort partners by score descending
        for fp in result:
            result[fp].sort(key=lambda x: x["co_change_count"], reverse=True)

        return dict(result)

    @staticmethod
    def _compute_percentiles(metadata_list: list[dict]) -> None:
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
