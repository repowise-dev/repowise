"""GitLab issues for the ``forge`` issue source, read through the REST API (v4).

Works the same on gitlab.com and a self-managed host. Per commit, one
lookup finds the merge request it came from: the merged one whose merge or
squash commit it is, else the only merged one that contains it. Each merge
request's closing issues come from ``closes_issues``; issues a commit message
closes itself (``Closes #12``) are read in batches of 100. GitLab has no
batched form of the first two, so they run a few at a time.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote, urlsplit

from ..sources import IssueCommit, IssueLink, SourceRefused
from . import ROLLUP, closing_mentions, first_closers, link_type
from .http import ForgeHttp

LABEL = "GitLab"
PER_PAGE = 100
#: Requests in flight at once. GitLab has no batch lookup by sha; eight keep a
#: thousand-commit window to a couple of minutes and far under gitlab.com's
#: 2,000 requests a minute.
WORKERS = 8

#: GitLab's default closing pattern, as it reads a commit message on the
#: default branch: ``Closes #12``, ``fixes: #12``, ``Implements #12``.
CLOSING_RE = re.compile(
    r"\b(?:clos(?:e[sd]?|ing)|fix(?:e[sd]|ing)?|resolv(?:e[sd]?|ing)|implement(?:s|ed|ing)?)\b"
    r":?\s+#(\d{1,9})\b",
    re.IGNORECASE,
)


def api_url(remote: Any) -> str:
    """The API root for *remote*'s host: ``https://gitlab.com/api/v4`` or the host's own."""
    parts = urlsplit(remote.web_base)
    return f"{parts.scheme}://{parts.netloc}/api/v4"


def fetch_links(
    commits: Sequence[IssueCommit],
    remote: Any,
    *,
    token: str,
    api_url: str,
    bug_labels: Iterable[str],
    transport: Any = None,
) -> Iterator[IssueLink]:
    """Every link GitLab can name for *commits*, each (commit, issue) once."""
    client = ForgeHttp(LABEL, {"PRIVATE-TOKEN": token}, transport=transport)
    path = "/".join((*remote.namespace, remote.repo))
    api = _Api(client, api_url.rstrip("/"), path)
    try:
        yield from _links(api, commits, bug_labels)
    finally:
        client.close()


def _links(
    api: _Api, commits: Sequence[IssueCommit], bug_labels: Iterable[str]
) -> Iterator[IssueLink]:
    labels = list(bug_labels)
    project_id = api.project_id()
    merge_of = api.merge_requests_for(commits)
    iids = sorted(set(merge_of.values()))
    closing = dict(zip(iids, _each(api.closes_issues, iids), strict=True))
    mentions = {c.sha: closing_mentions(c, CLOSING_RE) for c in commits}
    mentioned = api.issues(sorted({n for ns in mentions.values() for n in ns}))
    candidates = []
    for commit in commits:
        iid = merge_of.get(commit.sha)
        found = [(f"MR !{iid}", issue, f"MR !{iid}") for issue in closing.get(iid, ())]
        # A message on an MR's commit belongs to that MR's change.
        change = f"MR !{iid}" if iid is not None else commit.sha
        found += [
            (change, mentioned[n], "commit message") for n in mentions[commit.sha] if n in mentioned
        ]
        for via, issue, how in found:
            key = _key(issue, project_id)
            link = IssueLink(
                key=key,
                type=link_type(issue.get("issue_type"), issue.get("labels") or (), labels),
                sha=commit.sha,
                status=str(issue.get("state") or ""),
                title=str(issue.get("title") or ""),
                url=str(issue.get("web_url") or ""),
                basis=f"{how} closes {key}",
            )
            candidates.append((via, link))
    yield from first_closers(candidates)


def _key(issue: dict[str, Any], project_id: Any) -> str:
    """``#12`` for this project's issue, ``group/proj#12`` for another project's."""
    iid = issue.get("iid")
    if issue.get("project_id") == project_id:
        return f"#{iid}"
    web = urlsplit(str(issue.get("web_url") or "")).path
    other = web.split("/-/", 1)[0].strip("/")
    return f"{other}#{iid}" if other else f"#{iid}"


class _Api:
    """REST calls against one project."""

    def __init__(self, client: ForgeHttp, root: str, path: str) -> None:
        self.client, self.root, self.path = client, root, path
        self.project = f"{root}/projects/{quote(path, safe='')}"

    def _get(self, url: str, params: Any = None) -> Any:
        return self.client.send("GET", url, params=params)

    def project_id(self) -> Any:
        project = self._get(self.project)
        if not isinstance(project, dict):
            raise SourceRefused(
                f"GitLab found no project {self.path} at {self.root}: check the remote, the "
                "API URL and that the token can read the project."
            )
        return project.get("id")

    def merge_requests_for(self, commits: Sequence[IssueCommit]) -> dict[str, int]:
        """sha -> the one merged merge request each commit came from, where there is one.

        Looked up by sha, never read from the message: on a GitLab project a
        ``(#12)`` names issue 12 as often as anything, and history moved from
        another host carries that host's numbers.
        """
        found = _each(self._merge_request_of, [c.sha for c in commits])
        return {c.sha: iid for c, iid in zip(commits, found, strict=True) if iid is not None}

    def _merge_request_of(self, sha: str) -> int | None:
        found = self._get(
            f"{self.project}/repository/commits/{sha}/merge_requests", {"per_page": PER_PAGE}
        )
        mr = _merge_of(sha, found if isinstance(found, list) else [])
        return int(mr["iid"]) if mr is not None else None

    def closes_issues(self, iid: int) -> list[dict[str, Any]]:
        """The issues merge request *iid* closes; none for a rollup."""
        found = self._get(
            f"{self.project}/merge_requests/{int(iid)}/closes_issues", {"per_page": PER_PAGE}
        )
        issues = [i for i in found or () if isinstance(i, dict)]
        return issues if len(issues) < ROLLUP else []

    def issues(self, iids: list[int]) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for start in range(0, len(iids), PER_PAGE):
            chunk = iids[start : start + PER_PAGE]
            params = [("iids[]", str(n)) for n in chunk] + [("per_page", str(PER_PAGE))]
            for issue in self._get(f"{self.project}/issues", params) or ():
                if isinstance(issue, dict) and issue.get("iid") in chunk:
                    out[int(issue["iid"])] = issue
        return out


def _each(fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
    """``fn`` over *items* on a few threads, in order; the first failure stops the rest.

    Tasks check a shared flag before sending, so a rate limit or a refused
    token stops the queue at once instead of after every earlier task.
    """
    if not items:
        return []
    stop = threading.Event()

    def task(item: Any) -> Any:
        if stop.is_set():
            return None
        try:
            return fn(item)
        except BaseException:
            stop.set()
            raise

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(task, item) for item in items]
        for done in as_completed(futures):
            exc = done.exception()
            if exc is not None:
                for f in futures:
                    f.cancel()
                raise exc
        return [f.result() for f in futures]


def _merge_of(sha: str, found: list[Any]) -> dict[str, Any] | None:
    merged = [m for m in found if isinstance(m, dict) and m.get("state") == "merged"]
    for mr in merged:
        if sha in (mr.get("merge_commit_sha"), mr.get("squash_commit_sha")):
            return mr
    return merged[0] if len(merged) == 1 else None
