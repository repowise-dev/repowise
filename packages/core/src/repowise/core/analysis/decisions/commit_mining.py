"""Shared pieces of the two commit miners: git archaeology and PR bodies."""

from __future__ import annotations

from collections.abc import Sequence

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
#: body rather than a short one. Roughly twice the largest completion measured
#: on this repository; see decision ``1c228ed8``.
_BATCH_MAX_TOKENS = 8000

#: How many of a commit's files either commit prompt will show. The model has
#: to read the list to pick from it, and a commit that touched ninety files is
#: not one whose decisions can be assigned by reading the list anyway.
#:
#: Sorted before truncating at both call sites, so which files the model is
#: allowed to choose from is a property of the commit rather than of the order
#: ``_git_meta_map`` happened to be built in.
_MAX_PROMPT_FILES = 20

# PR/squash body markers — a body containing any of these reads like a PR
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
    list, under :data:`SCOPE_BASIS_SELECTED`. Falling back to the commit's
    whole footprint when the model selected nothing would reinstate exactly
    what this replaces, so an empty selection stays empty: the record keeps
    its commit, its evidence and its place in repository-wide answers, and
    stops answering "what governs this file". Roughly one record in six lands
    here, and every one of them measured as a record whose subject was not in
    the commit's list to begin with.

    The old breadth rule is the fallback for a *missing* answer rather than an
    empty one -- a provider that ignored the new key, or a cached response
    written before it existed. There the record is scoped as it always was and
    the legacy basis says so.
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
    # Scan subject + body for signals — squash-merge repos carry the
    # decision rationale in the body, not the one-line subject.
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
    # Cleared whether or not the sha resolved: an unattributed decision has no
    # commit to validate paths against, so the model's list is unusable rather
    # than merely unused.
    decision.proposed_files = None
    return sha
