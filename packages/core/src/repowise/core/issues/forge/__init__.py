"""The built-in ``forge`` issue source: GitHub and GitLab issues for the repository's own forge.

Registered by default and run only once ``REPOWISE_PLUGIN_FORGE_TOKEN`` is
set. For each commit in the defect window it finds the pull or merge request
the commit came from and the issues that request closes, plus issues the
commit message itself closes (``Fixes #12``). A bare ``#12`` is never read:
it names a pull request as often as an issue.

Types are precision first. ``bug`` or ``incident`` comes only from the
issue's own type (GitHub issue types, GitLab's incident type) or, for an
issue with none, from a label in ``plugins.forge.bug_labels``. Anything else
is ``feature``, ``task`` or ``other``, which change no count.

Every forge fact (API URLs, queries, field names, closing keywords) lives in
``github.py`` and ``gitlab.py`` beside this file. Azure Boards (``AB#12``)
and Bitbucket are not read: the source refuses those remotes by name.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from repowise.core.settings.areas.plugins import FORGE_API_URL, FORGE_BUG_LABELS, FORGE_TOKEN

from ..sources import IssueCommit, IssueLink, IssueRequest, SourceRefused

__all__ = ["ROLLUP", "ForgeIssues", "closing_mentions", "first_closers", "link_type"]

#: A pull or merge request closing this many issues or more is a rollup (a
#: release, a branch merge) that lists fixes made elsewhere: its links are
#: dropped. A release PR's changelog names every issue since the last one.
ROLLUP = 5

#: Issue types a forge names natively, folded to the seam's types.
_NATIVE_TYPES = {"bug": "bug", "incident": "incident", "feature": "feature", "task": "task"}


def link_type(native: str | None, labels: Iterable[str], bug_labels: Iterable[str]) -> str:
    """The seam type for an issue: its own type first, then a configured bug label.

    A native type outside the known four (GitHub's ``Epic``, GitLab's plain
    ``issue``) says nothing, so the labels decide. A label named
    ``incident`` gives ``incident``; any other configured label gives ``bug``.
    """
    kind = _NATIVE_TYPES.get((native or "").strip().lower())
    if kind is not None:
        return kind
    wanted = {str(label).strip().lower() for label in bug_labels}
    for label in labels:
        name = str(label).strip().lower()
        if name in wanted:
            return "incident" if name == "incident" else "bug"
    return "other"


def closing_mentions(commit: IssueCommit, pattern: Any) -> list[int]:
    """Issue numbers *commit*'s message closes with *pattern*, first mention first.

    A revert's message quotes the commit it undoes, closing words included,
    so a revert closes nothing. The commit's own change number is left out.
    """
    if commit.subject.startswith('Revert "'):
        return []
    text = f"{commit.subject}\n{commit.body}"
    found = dict.fromkeys(int(m.group(1)) for m in pattern.finditer(text))
    found.pop(commit.change, None)
    return list(found)


def first_closers(candidates: Iterable[tuple[str, IssueLink]]) -> list[IssueLink]:
    """One change per issue: the oldest that closes it, with each of its commits.

    *candidates* are ``(via, link)`` pairs in the request's commit order,
    newest first, where *via* names the change (``PR #12``, or the commit for
    a message that closes the issue itself). An issue closed again later, by
    a release or a merge of one branch into another, keeps only its first
    fix. Each (commit, issue) comes out once.
    """
    pairs = list(candidates)
    oldest = {link.key: via for via, link in pairs}  # the last seen is the oldest
    out: list[IssueLink] = []
    seen: set[tuple[str, str]] = set()
    for via, link in pairs:
        if oldest[link.key] == via and (link.sha, link.key) not in seen:
            seen.add((link.sha, link.key))
            out.append(link)
    return out


class ForgeIssues:
    """Issue links read from the repository's own GitHub or GitLab.

    *transport* stands in for the network in tests.
    """

    name = "forge"
    settings = (FORGE_TOKEN, FORGE_API_URL, FORGE_BUG_LABELS)

    def __init__(self, transport: Any = None) -> None:
        self._transport = transport

    def fetch(self, request: IssueRequest) -> Iterator[IssueLink]:
        from repowise.core.forges.base import ForgeKind
        from repowise.core.forges.registry import get_forge

        remote = request.remote
        if remote is None:
            raise SourceRefused(
                "this repository has no origin or upstream remote, so there is no "
                "GitHub or GitLab project to read issues from."
            )
        settings = request.settings or {}
        token = (request.credentials or {}).get(FORGE_TOKEN.key, "")
        api_url = str(settings.get(FORGE_API_URL.key) or "").strip()
        labels = settings.get(FORGE_BUG_LABELS.key, FORGE_BUG_LABELS.default)
        if remote.forge is ForgeKind.GITHUB:
            from . import github as forge_api
        elif remote.forge is ForgeKind.GITLAB:
            from . import gitlab as forge_api  # type: ignore[no-redef]
        else:
            label = get_forge(remote.forge).label
            raise SourceRefused(
                f"the forge issue source reads GitHub and GitLab, and this repository's "
                f"remote is on {label}. For a self-managed GitHub or GitLab, map its host "
                "in forges.hosts."
            )
        yield from forge_api.fetch_links(
            request.commits,
            remote,
            token=token,
            api_url=api_url or forge_api.api_url(remote),
            bug_labels=labels,
            transport=self._transport,
        )
