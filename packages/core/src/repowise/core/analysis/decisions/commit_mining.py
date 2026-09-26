"""Shared pieces of the two commit miners: git archaeology and PR bodies."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from repowise.core.analysis.decisions.scope import (
    SCOPE_BASIS_SELECTED,
    commit_scope_basis,
    commit_scope_files,
    selected_scope_files,
)

from .commit_signals import count_decision_signals
from .records import ExtractedDecision

#: The output budget for one batch of either commit prompt. Reasoning tokens
#: are charged to it, so a budget that only fits the answer buys an empty
#: body rather than a short one; see decision ``1c228ed8``.
_BATCH_MAX_TOKENS = 8000

#: How many of a commit's files either commit prompt will show. Sorted before
#: truncating, so the files offered depend on the commit, not on map order.
_MAX_PROMPT_FILES = 20

# PR/squash body markers: a body containing any of these reads like a PR
# description worth mining (vs an incidental multi-line commit message).
_PR_BODY_MARKERS = (
    "## why",
    "## motivation",
    "## what",
    "## changes",
    "## context",
    "## summary",
    "closes #",
    "fixes #",
    "resolves #",
    "before:",
    "after:",
)
_MAX_PR_BODIES = 25


def _scope_from_selection(
    decision: ExtractedDecision,
    commit_files: Sequence[str] | None,
) -> tuple[list[str], str]:
    """The files and basis for one decision mined out of one commit.

    Returns the model's own selection, validated against the commit's file
    list, under :data:`SCOPE_BASIS_SELECTED`. An empty selection stays empty:
    the record keeps its commit and evidence but governs no file. Only a
    *missing* answer falls back to the commit-wide scope.
    """
    if decision.proposed_files is None:
        return (commit_scope_files(commit_files), commit_scope_basis(commit_files))
    chosen = selected_scope_files(decision.proposed_files, commit_files)
    decision.proposed_files = None
    return (chosen, SCOPE_BASIS_SELECTED)


def _signal_commit_info(commit: dict) -> dict | None:
    """The prompt-ready record of a commit with decision signals, else None."""
    msg = commit.get("message", "")
    body = commit.get("body", "")
    # Scan subject and body: squash-merge repos carry the rationale in the body.
    signal_count = count_decision_signals(f"{msg}\n{body}".lower())
    if signal_count <= 0:
        return None
    return {
        "sha": commit.get("sha", ""),
        "message": msg,
        "body": body,
        "author": commit.get("author", ""),
        "date": commit.get("date", ""),
        "signal_count": signal_count,
    }


def _attribute_to_commit(
    decision: ExtractedDecision,
    batch: list[dict],
    subject_key: str,
    files_by_sha: dict[str, list[str]],
    source_by_sha: dict[str, str],
) -> str:
    """Bind a decision mined from a batch of commits to the commit it came from.

    Prefers the sha the model reported, else the first commit whose subject
    (``batch[i][subject_key]``, first 40 chars) appears in the title. Scopes
    the decision to that commit's files and returns the sha, or ``""`` when
    nothing matched.
    """
    sha = decision.evidence_commits[0] if decision.evidence_commits else ""
    if not sha:
        # Try to match back to a commit
        for c in batch:
            if c[subject_key][:40].lower() in decision.title.lower():
                sha = c["sha"]
                break
    if sha:
        decision.evidence_commits = [sha]
        decision.affected_files, decision.scope_basis = _scope_from_selection(
            decision, files_by_sha.get(sha)
        )
        decision.source_text = source_by_sha.get(sha, "")
    # Cleared either way: an unattributed decision has no commit to validate
    # the model's paths against.
    decision.proposed_files = None
    return sha


def _significant_commits(meta: dict) -> Any:
    """A file's ``significant_commits_json`` as the miner reads it.

    A string that does not decode yields no commits; anything else passes
    through as-is.
    """
    commits_json = meta.get("significant_commits_json", "[]")
    if not isinstance(commits_json, str):
        return commits_json
    try:
        return json.loads(commits_json)
    except (json.JSONDecodeError, TypeError):
        return []


def _admit_signal_commit(commit: dict, commit_map: dict[str, dict]) -> bool:
    """Whether *commit*'s file should be counted, adding it to *commit_map* on first sight.

    A new sha is admitted only when it carries decision signals.
    """
    sha = commit.get("sha", "")
    if not sha or sha in commit_map:
        return True
    info = _signal_commit_info(commit)
    if info is None:
        return False
    commit_map[sha] = info
    return True


def signal_commits(
    git_meta_map: dict[str, dict],
) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Unique significant commits carrying decision signals, and each one's files.

    Returns ``(sha -> commit info, sha -> files it touched)``.
    """
    commit_map: dict[str, dict] = {}
    commit_files: dict[str, list[str]] = {}
    for file_path, meta in git_meta_map.items():
        for commit in _significant_commits(meta):
            if _admit_signal_commit(commit, commit_map):
                commit_files.setdefault(commit.get("sha", ""), []).append(file_path)
    return commit_map, commit_files


def _loads_commits(value: Any) -> list[dict]:
    """Parse a ``significant_commits_json`` blob into a list of dicts."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
        return data if isinstance(data, list) else []
    return []


def _pr_candidate(commit: dict, body: str) -> dict | None:
    """The prompt-ready record of a PR-shaped body with decision signals, else None."""
    low = body.lower()
    is_prish = commit.get("pr_number") is not None or any(m in low for m in _PR_BODY_MARKERS)
    if not is_prish or count_decision_signals(low) <= 0:
        return None
    return {
        "sha": commit["sha"],
        "subject": commit.get("message", ""),
        "body": body,
        "pr": commit.get("pr_number"),
    }


def pr_candidates(
    git_meta_map: dict[str, dict],
) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """PR / squash bodies worth mining, and every commit's files.

    Returns ``(sha -> candidate, sha -> files it touched)``.
    """
    candidates: dict[str, dict] = {}
    files_by_sha: dict[str, list[str]] = {}
    for fp, meta in git_meta_map.items():
        for c in _loads_commits(meta.get("significant_commits_json")):
            sha = c.get("sha", "")
            if not sha:
                continue
            files_by_sha.setdefault(sha, []).append(fp)
            body = (c.get("body") or "").strip()
            if sha in candidates or not body:
                continue
            candidate = _pr_candidate(c, body)
            if candidate is not None:
                candidates[sha] = candidate
    return candidates, files_by_sha


def _prompt_files(files: list[str]) -> str:
    return ", ".join(sorted(files)[:_MAX_PROMPT_FILES])


def git_commit_block(commit: dict, body: str, files: list[str]) -> str:
    """One commit's entry in the git archaeology prompt."""
    body_block = f"Body: {body[:1500]}\n" if body else ""
    return (
        f"\n--- Commit {commit['sha'][:8]} ---\n"
        f"Message: {commit['message']}\n"
        f"{body_block}"
        f"Author: {commit['author']}\n"
        f"Date: {commit['date']}\n"
        f"Files changed: "
        f"{_prompt_files(files)}\n"
    )


def pr_commit_block(candidate: dict, files: list[str]) -> str:
    """One PR body's entry in the PR mining prompt."""
    pr_label = f" (PR #{candidate['pr']})" if candidate.get("pr") else ""
    return (
        f"\n--- Commit {candidate['sha'][:8]}{pr_label} ---\n"
        f"Subject: {candidate['subject']}\n"
        f"Body:\n{candidate['body'][:2000]}\n"
        f"Files changed: {_prompt_files(files)}\n"
    )
