"""GitHub issues for the ``forge`` issue source, read through the GraphQL API.

Three batched rounds, each a handful of queries:

1. Every commit is looked up by sha, 50 to a query. A commit takes the merged
   pull request whose merge (or squash) commit it is, else the only merged
   one that contains it; a commit in two merged pull requests takes none.
   The number in the message is not trusted: history moved from another
   host carries that host's numbers.
2. Each pull request's closing issue references (keywords in its
   description, or issues linked in the sidebar).
3. Issues a commit message closes itself (``Fixes #12``), checked to be
   issues and not pull requests.

Ceiling: an issue's first 20 labels are read; more is rare enough that
paging is left out. A pull request closing :data:`ROLLUP` issues or more is
dropped whole, so its first 10 are enough to tell.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from ..sources import IssueCommit, IssueLink, SourceRefused, SourceUnavailable
from . import ROLLUP, closing_mentions, first_closers, link_type
from .http import ForgeHttp, retry_point

PUBLIC_API = "https://api.github.com/graphql"
LABEL = "GitHub"

#: GitHub's closing keywords, as it reads them in a commit message on the
#: default branch: ``Fixes #12``, ``closes: #12``.
CLOSING_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+#(\d{1,9})\b", re.IGNORECASE
)

#: Aliases per query. Each round costs a few points of the 5,000 an hour.
BATCH = 50

_ISSUE_FIELDS = """
  number title url state
  issueType { name }
  repository { nameWithOwner }
  labels(first: 20) { nodes { name } }
"""


def api_url(remote: Any) -> str:
    """The GraphQL endpoint for *remote*: api.github.com, or the Enterprise host's own."""
    if not remote.is_self_hosted:
        return PUBLIC_API
    parts = urlsplit(remote.web_base)
    return f"{parts.scheme}://{parts.netloc}/api/graphql"


def fetch_links(
    commits: Sequence[IssueCommit],
    remote: Any,
    *,
    token: str,
    api_url: str,
    bug_labels: Iterable[str],
    transport: Any = None,
) -> Iterator[IssueLink]:
    """Every link GitHub can name for *commits*, each (commit, issue) once."""
    client = ForgeHttp(LABEL, {"Authorization": f"bearer {token}"}, transport=transport)
    api = _Api(client, api_url, remote.namespace[0], remote.repo)
    try:
        yield from _links(api, commits, f"{remote.namespace[0]}/{remote.repo}", bug_labels)
    finally:
        client.close()


def _links(
    api: _Api, commits: Sequence[IssueCommit], own: str, bug_labels: Iterable[str]
) -> Iterator[IssueLink]:
    labels = list(bug_labels)
    pull_of = api.pulls_for([c.sha for c in commits])
    closing = api.closing_issues(sorted(set(pull_of.values())))
    mentions = {c.sha: closing_mentions(c, CLOSING_RE) for c in commits}
    mentioned = api.issues(sorted({n for ns in mentions.values() for n in ns}))
    candidates = []
    for commit in commits:
        number = pull_of.get(commit.sha)
        found = [(f"PR #{number}", issue, f"PR #{number}") for issue in closing.get(number, ())]
        # A message on a PR's commit belongs to that PR's change.
        change = f"PR #{number}" if number is not None else commit.sha
        found += [
            (change, mentioned[n], "commit message") for n in mentions[commit.sha] if n in mentioned
        ]
        for via, issue, how in found:
            key = _key(issue, own)
            candidates.append((via, _link(commit.sha, key, issue, f"{how} closes {key}", labels)))
    yield from first_closers(candidates)


def _key(issue: dict[str, Any], own: str) -> str:
    repo = (issue.get("repository") or {}).get("nameWithOwner") or own
    number = issue.get("number")
    return f"#{number}" if repo.lower() == own.lower() else f"{repo}#{number}"


def _link(sha: str, key: str, issue: dict[str, Any], basis: str, labels: list[str]) -> IssueLink:
    names = [n.get("name", "") for n in ((issue.get("labels") or {}).get("nodes") or ()) if n]
    native = (issue.get("issueType") or {}).get("name")
    return IssueLink(
        key=key,
        type=link_type(native, names, labels),
        sha=sha,
        status=str(issue.get("state") or "").lower(),
        title=str(issue.get("title") or ""),
        url=str(issue.get("url") or ""),
        basis=basis,
    )


class _Api:
    """Batched GraphQL queries against one repository."""

    def __init__(self, client: ForgeHttp, url: str, owner: str, name: str) -> None:
        self.client, self.url, self.owner, self.name = client, url, owner, name

    def pulls_for(self, shas: list[str]) -> dict[str, int]:
        """sha -> the one merged pull request it came from, for commits that have one."""
        out: dict[str, int] = {}
        for chunk in _chunks(shas, BATCH):
            fields = "\n".join(
                f'c{i}: object(oid: "{sha}") {{ ... on Commit {{ associatedPullRequests(first: 5) '
                "{ nodes { number merged mergeCommit { oid } } } } }"
                for i, sha in enumerate(chunk)
            )
            repo = self._query(fields)
            for i, sha in enumerate(chunk):
                number = _pull_of(sha, repo.get(f"c{i}"))
                if number is not None:
                    out[sha] = number
        return out

    def closing_issues(self, numbers: list[int]) -> dict[int, list[dict[str, Any]]]:
        """Pull request number -> the issues it closes; merged ones that are no rollup."""
        out: dict[int, list[dict[str, Any]]] = {}
        for chunk in _chunks(numbers, BATCH):
            fields = "\n".join(
                f"p{i}: pullRequest(number: {int(n)}) {{ merged closingIssuesReferences(first: 10) "
                f"{{ nodes {{ {_ISSUE_FIELDS} }} }} }}"
                for i, n in enumerate(chunk)
            )
            repo = self._query(fields)
            for i, n in enumerate(chunk):
                pull = repo.get(f"p{i}") or {}
                nodes = (pull.get("closingIssuesReferences") or {}).get("nodes") or []
                if pull.get("merged") and 0 < len(nodes) < ROLLUP:
                    out[n] = [node for node in nodes if node]
        return out

    def issues(self, numbers: list[int]) -> dict[int, dict[str, Any]]:
        """Issue number -> the issue, for numbers that are issues (not pull requests)."""
        out: dict[int, dict[str, Any]] = {}
        for chunk in _chunks(numbers, BATCH):
            fields = "\n".join(
                f"i{i}: issueOrPullRequest(number: {int(n)}) {{ ... on Issue {{ {_ISSUE_FIELDS} }} }}"
                for i, n in enumerate(chunk)
            )
            repo = self._query(fields)
            for i, n in enumerate(chunk):
                issue = repo.get(f"i{i}") or {}
                if issue.get("number") == n:
                    out[n] = issue
        return out

    def _query(self, fields: str) -> dict[str, Any]:
        """The ``repository`` object of one query; an alias GitHub cannot resolve is None."""
        query = "query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { %s } }"
        answer = self.client.send(
            "POST",
            self.url,
            json={"query": query % fields, "variables": {"owner": self.owner, "name": self.name}},
        )
        if answer is None:
            raise SourceRefused(f"GitHub found nothing at {self.url} (404): check the API URL.")
        errors = answer.get("errors") or []
        _raise_for(errors, self.owner, self.name, self.client.last_headers)
        repo = (answer.get("data") or {}).get("repository")
        if repo is None:
            raise SourceRefused(
                f"GitHub found no repository {self.owner}/{self.name}: check the remote and "
                "that the token can read it."
            )
        return repo


def _raise_for(
    errors: list[dict[str, Any]], owner: str, name: str, headers: Mapping[str, str]
) -> None:
    """Raise unless every error only says one object is missing or hidden.

    A NOT_FOUND or FORBIDDEN below ``repository`` (a pull request number that
    is an issue, a closing issue in a private repository) leaves that object
    null and the rest stands. Any other error means the answer may be partial,
    and a fetch that completes on a partial answer would drop stored links.
    """
    for error in errors:
        kind = error.get("type")
        path = error.get("path") or []
        if kind in ("NOT_FOUND", "FORBIDDEN") and len(path) > 1:
            continue
        if kind == "RATE_LIMITED":
            at = retry_point(headers)
            when = f"after {at:%Y-%m-%d %H:%M} UTC" if at else "within the hour"
            raise SourceUnavailable(f"GitHub's rate limit is used up; sync again {when}.")
        if kind in ("FORBIDDEN", "UNAUTHORIZED", "NOT_FOUND"):
            raise SourceRefused(
                f"GitHub refused to read {owner}/{name} ({kind}): the token needs read access "
                "to its pull requests and issues."
            )
        raise SourceUnavailable(
            f"GitHub answered with an error ({kind or 'untyped'}); sync again later."
        )


def _pull_of(sha: str, commit: dict[str, Any] | None) -> int | None:
    nodes = ((commit or {}).get("associatedPullRequests") or {}).get("nodes") or []
    merged = [n for n in nodes if n and n.get("merged")]
    for pull in merged:
        if (pull.get("mergeCommit") or {}).get("oid") == sha:
            return int(pull["number"])
    return int(merged[0]["number"]) if len(merged) == 1 else None


def _chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]
