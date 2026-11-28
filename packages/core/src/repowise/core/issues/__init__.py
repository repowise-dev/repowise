"""Tracker issues linked to commits: the ``issue-source`` seam and its host side.

The built-in ``forge`` source (GitHub and GitLab issues) is registered here,
and runs only once its token is set.
"""

from .sources import (
    DEFECT_TYPES,
    ISSUE_TYPES,
    IssueCommit,
    IssueLink,
    IssueRequest,
    IssueSource,
    SourceRefused,
    SourceUnavailable,
    StoredLink,
    issue_sources,
    number_refs,
    resolve_links,
    run_issue_fetch,
    tracker_keys,
)

__all__ = [
    "DEFECT_TYPES",
    "ISSUE_TYPES",
    "IssueCommit",
    "IssueLink",
    "IssueRequest",
    "IssueSource",
    "SourceRefused",
    "SourceUnavailable",
    "StoredLink",
    "issue_sources",
    "number_refs",
    "resolve_links",
    "run_issue_fetch",
    "tracker_keys",
]

from .forge import ForgeIssues

issue_sources.register(ForgeIssues.name, ForgeIssues())
