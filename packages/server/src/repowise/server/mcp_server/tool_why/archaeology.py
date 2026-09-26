"""Git archaeology: the fallback evidence when no decision governs a file."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any

_GIT_LOG_FORMAT = "--format=%H\t%an\t%ai\t%s"


async def _git_archaeology_fallback(
    file_path: str,
    git_meta: Any | None,
    all_git_meta: list,
    repository: Any,
) -> dict:
    """When no decisions govern a file, mine git history for intent signals."""
    result: dict[str, Any] = {"triggered": True}

    # --- Layer 1: File's own significant commits ---
    file_commits = _file_commits(git_meta)
    result["file_commits"] = file_commits

    # --- Layer 2: Cross-file search — other files' commits mentioning this file ---
    basename, stem, search_terms = _search_terms(file_path)
    unique_refs = _cross_references(file_path, basename, search_terms, all_git_meta)
    result["cross_references"] = unique_refs

    # --- Layer 3: Live git log (when local repo exists) ---
    git_log_results = []
    local_path = getattr(repository, "local_path", None)
    if local_path and (Path(local_path) / ".git").exists():
        git_log_results = await _run_git_log(local_path, file_path, stem)
    result["git_log"] = git_log_results

    # --- Summary ---
    result["summary"] = _archaeology_summary(
        file_path, len(file_commits), len(unique_refs), len(git_log_results)
    )
    return result


def _commit_row(c: dict[str, Any]) -> dict[str, Any]:
    """A stored significant commit as an archaeology row."""
    return {
        "sha": c.get("sha", ""),
        "message": c.get("message", ""),
        "author": c.get("author", ""),
        "date": c.get("date", ""),
    }


def _file_commits(git_meta: Any | None) -> list[dict[str, Any]]:
    """The file's own significant commits, as indexed."""
    if not (git_meta and git_meta.significant_commits_json):
        return []
    return [_commit_row(c) for c in json.loads(git_meta.significant_commits_json)]


def _search_terms(file_path: str) -> tuple[str, str, set[str]]:
    """``(basename, stem, terms)`` a commit message can name this file by."""
    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    # Convert snake_case/kebab to searchable terms: auth_cache_service -> {"auth", "cache", "service"}
    search_terms = set(re.split(r"[_\-/.]", stem.lower()))
    search_terms.discard("")
    # Also search for the full basename
    search_terms.add(basename.lower())
    return basename, stem, search_terms


def _mentions(msg_lower: str, term: str) -> bool:
    """Whether *term* occurs in *msg_lower* as a whole token, so ``auth`` misses ``author``."""
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", msg_lower) is not None


def _cross_references(
    file_path: str, basename: str, search_terms: set[str], all_git_meta: list
) -> list[dict[str, Any]]:
    """Other files' commits that mention this one, one per SHA, newest first."""
    cross_references = []
    for gm in all_git_meta:
        if gm.file_path == file_path:
            continue
        commits = json.loads(gm.significant_commits_json) if gm.significant_commits_json else []
        for c in commits:
            msg_lower = c.get("message", "").lower()
            # Match if the commit message mentions the file basename or 2+ stem terms
            matched_terms = sorted(t for t in search_terms if _mentions(msg_lower, t))
            if _mentions(msg_lower, basename.lower()) or len(matched_terms) >= 2:
                cross_references.append(
                    {"source_file": gm.file_path, **_commit_row(c), "matched_terms": matched_terms}
                )
    # Deduplicate by SHA and sort by date descending
    seen_shas: set[str] = set()
    unique_refs = []
    for cr in cross_references:
        if cr["sha"] not in seen_shas:
            seen_shas.add(cr["sha"])
            unique_refs.append(cr)
    unique_refs.sort(key=lambda x: x.get("date", ""), reverse=True)
    return unique_refs


def _archaeology_summary(
    file_path: str, file_commits: int, cross_references: int, git_log: int
) -> str:
    """One sentence counting what each layer recovered, or saying none did."""
    if file_commits + cross_references + git_log > 0:
        return (
            f"No architectural decisions found for {file_path}, but git archaeology "
            f"recovered {file_commits} direct commit(s), "
            f"{cross_references} cross-reference(s), and "
            f"{git_log} git log result(s). "
            "Review these to understand the intent behind this code."
        )
    return (
        f"No architectural decisions or git history found for {file_path}. "
        "This file may be new or not yet indexed."
    )


async def _run_git_log(
    repo_path: str,
    file_path: str,
    stem: str,
) -> list[dict]:
    """Run git log against the local repo for deeper history. Best-effort."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_sync_git_log, repo_path, file_path, stem), timeout=15
        )
    except TimeoutError:
        return []


def _sync_git_log(repo_path: str, file_path: str, stem: str) -> list[dict]:
    """The file's own log, then commits whose message names its stem."""
    results: list[dict] = []
    # Sanitize stem to prevent argument injection via --grep
    safe_stem = re.sub(r"[^a-zA-Z0-9_\-.]", "", stem) if stem else ""
    try:
        stdout = _git_log_stdout(
            repo_path, ["--follow", _GIT_LOG_FORMAT, "-20", "--", file_path]
        )
        if stdout is not None:
            results.extend(_parse_git_log(stdout, "git_log_follow"))

        if safe_stem and len(safe_stem) >= 3:
            stdout = _git_log_stdout(
                repo_path,
                [
                    "--all",
                    "--grep",
                    safe_stem,
                    _GIT_LOG_FORMAT,
                    "-10",
                    "--",  # end of options — prevent argument injection
                ],
            )
            if stdout is not None:
                seen = {r["sha"] for r in results}
                results.extend(_parse_git_log(stdout, "git_log_grep", seen))
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return results


def _git_log_stdout(repo_path: str, args: list[str]) -> str | None:
    """``git log <args>`` output, or ``None`` when git exits non-zero."""
    proc = subprocess.run(
        ["git", "log", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        # Git emits utf-8; text=True alone uses the locale codec (cp1252 on Windows).
        encoding="utf-8",
        errors="replace",
        timeout=10,
        # A git child inheriting the JSON-RPC stdin can wedge the session
        # (see commits_since() in core/precedent/currency.py).
        stdin=subprocess.DEVNULL,
    )
    return proc.stdout if proc.returncode == 0 else None


def _parse_git_log(
    stdout: str, source: str, seen: set[str] | None = None
) -> list[dict]:
    """Rows from ``_GIT_LOG_FORMAT`` output; with *seen*, skip SHAs already in it."""
    rows: list[dict] = []
    for line in stdout.strip().splitlines():
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        sha = parts[0][:12]
        if seen is not None:
            if sha in seen:
                continue
            seen.add(sha)
        rows.append(
            {
                "sha": sha,
                "commit": parts[0],
                "author": parts[1],
                "date": parts[2][:10],
                "message": parts[3],
                "source": source,
            }
        )
    return rows
