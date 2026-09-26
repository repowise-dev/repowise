"""Git archaeology: the fallback evidence when no decision governs a file."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from repowise.server.mcp_server._budget import (
    OmissionCollector,
)


async def _git_archaeology_fallback(
    file_path: str,
    git_meta: Any | None,
    all_git_meta: list,
    repository: Any,
    collector: OmissionCollector | None = None,
) -> dict:
    """When no decisions govern a file, mine git history for intent signals."""
    result: dict[str, Any] = {"triggered": True}

    # --- Layer 1: File's own significant commits ---
    file_commits = []
    if git_meta and git_meta.significant_commits_json:
        commits = json.loads(git_meta.significant_commits_json)
        file_commits = [
            {
                "sha": c.get("sha", ""),
                "message": c.get("message", ""),
                "author": c.get("author", ""),
                "date": c.get("date", ""),
            }
            for c in commits
        ]
    result["file_commits"] = file_commits

    # --- Layer 2: Cross-file search — other files' commits mentioning this file ---
    basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    # Convert snake_case/kebab to searchable terms: auth_cache_service -> {"auth", "cache", "service"}
    search_terms = set(re.split(r"[_\-/.]", stem.lower()))
    search_terms.discard("")
    # Also search for the full basename
    search_terms.add(basename.lower())

    cross_references = []
    for gm in all_git_meta:
        if gm.file_path == file_path:
            continue
        commits = json.loads(gm.significant_commits_json) if gm.significant_commits_json else []
        for c in commits:
            msg_lower = c.get("message", "").lower()
            # Match if the commit message mentions the file basename or 2+ stem terms
            matched_terms = sorted(t for t in search_terms if t in msg_lower)
            if basename.lower() in msg_lower or len(matched_terms) >= 2:
                cross_references.append(
                    {
                        "source_file": gm.file_path,
                        "sha": c.get("sha", ""),
                        "message": c.get("message", ""),
                        "author": c.get("author", ""),
                        "date": c.get("date", ""),
                        "matched_terms": matched_terms,
                    }
                )
    # Deduplicate by SHA and sort by date descending
    seen_shas: set[str] = set()
    unique_refs = []
    for cr in cross_references:
        if cr["sha"] not in seen_shas:
            seen_shas.add(cr["sha"])
            unique_refs.append(cr)
    unique_refs.sort(key=lambda x: x.get("date", ""), reverse=True)
    result["cross_references"] = unique_refs

    # --- Layer 3: Live git log (when local repo exists) ---
    git_log_results = []
    local_path = getattr(repository, "local_path", None)
    if local_path and (Path(local_path) / ".git").exists():
        git_log_results = await _run_git_log(local_path, file_path, stem)
    result["git_log"] = git_log_results

    # --- Summary ---
    total = len(file_commits) + len(unique_refs) + len(git_log_results)
    if total > 0:
        result["summary"] = (
            f"No architectural decisions found for {file_path}, but git archaeology "
            f"recovered {len(file_commits)} direct commit(s), "
            f"{len(unique_refs)} cross-reference(s), and "
            f"{len(git_log_results)} git log result(s). "
            "Review these to understand the intent behind this code."
        )
    else:
        result["summary"] = (
            f"No architectural decisions or git history found for {file_path}. "
            "This file may be new or not yet indexed."
        )

    return result


async def _run_git_log(
    repo_path: str,
    file_path: str,
    stem: str,
) -> list[dict]:
    """Run git log against the local repo for deeper history. Best-effort."""
    import asyncio
    import subprocess

    def _sync_git_log() -> list[dict]:
        import re

        results: list[dict] = []
        # Sanitize stem to prevent argument injection via --grep
        safe_stem = re.sub(r"[^a-zA-Z0-9_\-.]", "", stem) if stem else ""
        try:
            proc = subprocess.run(
                ["git", "log", "--follow", "--format=%H\t%an\t%ai\t%s", "-20", "--", file_path],
                cwd=repo_path,
                capture_output=True,
                text=True,
                # ``%s`` is the commit subject and ``%an`` the author name, both
                # utf-8 from git. text=True alone decodes with the locale codec,
                # which is cp1252 on a default Windows install.
                encoding="utf-8",
                errors="replace",
                timeout=10,
                # See commits_since() in core/precedent/currency.py: a git child
                # that inherits this server's JSON-RPC stdin can wedge the
                # session, and the timeout above is not a reliable ceiling.
                stdin=subprocess.DEVNULL,
            )
            if proc.returncode == 0:
                for line in proc.stdout.strip().splitlines():
                    parts = line.split("\t", 3)
                    if len(parts) == 4:
                        results.append(
                            {
                                "sha": parts[0][:12],
                                "commit": parts[0],
                                "author": parts[1],
                                "date": parts[2][:10],
                                "message": parts[3],
                                "source": "git_log_follow",
                            }
                        )

            if safe_stem and len(safe_stem) >= 3:
                proc2 = subprocess.run(
                    [
                        "git",
                        "log",
                        "--all",
                        "--grep",
                        safe_stem,
                        "--format=%H\t%an\t%ai\t%s",
                        "-10",
                        "--",  # end of options — prevent argument injection
                    ],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",  # see above
                    errors="replace",
                    timeout=10,
                    stdin=subprocess.DEVNULL,  # see above
                )
                if proc2.returncode == 0:
                    seen = {r["sha"] for r in results}
                    for line in proc2.stdout.strip().splitlines():
                        parts = line.split("\t", 3)
                        if len(parts) == 4 and parts[0][:12] not in seen:
                            seen.add(parts[0][:12])
                            results.append(
                                {
                                    "sha": parts[0][:12],
                                    "commit": parts[0],
                                    "author": parts[1],
                                    "date": parts[2][:10],
                                    "message": parts[3],
                                    "source": "git_log_grep",
                                }
                            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return results

    try:
        return await asyncio.wait_for(asyncio.to_thread(_sync_git_log), timeout=15)
    except TimeoutError:
        return []
