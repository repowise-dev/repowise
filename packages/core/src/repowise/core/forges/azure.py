"""Azure DevOps (Services and Server).

Services answers on three host shapes for one repo: ``dev.azure.com/org``,
``ssh.dev.azure.com:v3/org`` and the legacy ``org.visualstudio.com``. All three
fold to ``dev.azure.com`` so a repo has one host, one web base and one key.
Server (on-prem, formerly TFS) keeps its host and whatever collection path
sits before ``_git``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

from .base import (
    BaseForge,
    CiSystem,
    ForgeKind,
    RemoteParts,
    RemoteRef,
    collect_refs,
    join_url,
    looks_like_sha,
    quote_path,
    quote_segment,
)
from .registry import register

PUBLIC_HOST = "dev.azure.com"
_PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
_SSH_HOSTS = frozenset({"ssh.dev.azure.com", "vs-ssh.visualstudio.com"})
_LEGACY_SUFFIX = ".visualstudio.com"
_GIT = "_git"

# Squash and merge completions write ``Merged PR 12: title``; older Server
# merges write ``Merge pull request 12 from x into main``.
_SUBJECT_REF_RES = (
    re.compile(r"^Merged PR (\d{1,9}):"),
    re.compile(r"^Merge pull request (\d{1,9}) from "),
)
_URL_REF_RE = re.compile(r"/pullrequest/(\d{1,9})\b", re.IGNORECASE)


def _project_repo(rest: tuple[str, ...]) -> tuple[str, str] | None:
    """``(project, repo)`` from ``project/_git/repo``, or ``_git/repo`` when they share a name."""
    if len(rest) >= 3 and rest[1].lower() == _GIT:
        return rest[0], rest[2]
    if len(rest) >= 2 and rest[0].lower() == _GIT:
        return rest[1], rest[1]
    return None


def _version(rev: str) -> str:
    """Azure's version selector: ``GC`` for a commit, ``GB`` for a branch."""
    return ("GC" if looks_like_sha(rev) else "GB") + quote(rev, safe="/")


@dataclass(frozen=True, slots=True)
class Azure(BaseForge):
    def claims_host(self, host: str) -> bool:
        return host == PUBLIC_HOST or host in _SSH_HOSTS or host.endswith(_LEGACY_SUFFIX)

    def _cloud(self, org: str, project: str, repo: str) -> RemoteRef:
        web_base = join_url(_PUBLIC_ORIGIN, org, project, _GIT, repo)
        return RemoteRef(self.kind, PUBLIC_HOST, (org, project), repo, web_base, False)

    def parse(self, parts: RemoteParts) -> RemoteRef | None:
        host, segs = parts.host, parts.segments
        if host in _SSH_HOSTS:
            if len(segs) >= 4 and segs[0].lower() == "v3":
                return self._cloud(segs[1], segs[2], segs[3])
            return None
        if host == PUBLIC_HOST:
            pr = _project_repo(segs[1:]) if segs else None
            return self._cloud(segs[0], *pr) if pr else None
        if host.endswith(_LEGACY_SUFFIX):
            rest = segs[1:] if segs and segs[0].lower() == "defaultcollection" else segs
            pr = _project_repo(rest)
            return self._cloud(host[: -len(_LEGACY_SUFFIX)], *pr) if pr else None
        # Server: everything before `_git` is the collection and project.
        lowered = [s.lower() for s in segs]
        if _GIT not in lowered:
            return None
        i = lowered.index(_GIT)
        if i == 0 or i + 1 >= len(segs):
            return None
        web_base = join_url(parts.web_origin, *segs[:i], _GIT, segs[i + 1])
        return RemoteRef(self.kind, host, segs[:i], segs[i + 1], web_base, True)

    def canonical_path(self, parts: RemoteParts) -> str | None:
        ref = self.parse(parts)
        if ref is None or ref.is_self_hosted:
            return None
        return ref.web_base.removeprefix("https://")

    def commit_url(self, ref: RemoteRef, sha: str) -> str:
        return f"{ref.web_base}/commit/{quote_segment(sha)}"

    def change_url(self, ref: RemoteRef, number: int) -> str:
        return f"{ref.web_base}/pullrequest/{number}"

    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str:
        # The shape Azure's own "copy link to line" produces.
        url = f"{ref.web_base}?path=/{quote_path(path)}&version={_version(rev)}"
        if line:
            url += (
                f"&line={line}&lineEnd={line + 1}"
                "&lineStartColumn=1&lineEndColumn=1&lineStyle=plain"
            )
        return url + "&_a=contents"

    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str:
        return (
            f"{ref.web_base}/branchCompare?baseVersion={_version(base)}"
            f"&targetVersion={_version(head)}"
        )

    def format_change_ref(self, number: int) -> str:
        return f"PR {number}"

    def parse_change_refs(self, subject: str, body: str = "", *, native: bool = True) -> list[int]:
        return collect_refs(subject, body, _SUBJECT_REF_RES, (_URL_REF_RE,))


register(
    Azure(
        kind=ForgeKind.AZURE,
        label="Azure DevOps",
        ci=CiSystem(
            name="azure_pipelines",
            markers=("TF_BUILD", "SYSTEM_TEAMFOUNDATIONCOLLECTIONURI"),
            # TARGETBRANCH is the full ref; the NAME form is newer and bare.
            base_branch=("SYSTEM_PULLREQUEST_TARGETBRANCHNAME", "SYSTEM_PULLREQUEST_TARGETBRANCH"),
            head_sha=("BUILD_SOURCEVERSION",),
            # NUMBER is set only for GitHub repos, whose ID is an internal id
            # rather than the number; for Azure Repos the ID is the number.
            change_number=(
                "SYSTEM_PULLREQUEST_PULLREQUESTNUMBER",
                "SYSTEM_PULLREQUEST_PULLREQUESTID",
            ),
            repo_url=(("BUILD_REPOSITORY_URI",),),
            # Pipelines builds repos hosted anywhere; "Git" (any other host) is
            # left to the repo URL.
            provider_var="BUILD_REPOSITORY_PROVIDER",
            providers=(
                ("tfsgit", ForgeKind.AZURE),
                ("github", ForgeKind.GITHUB),
                ("githubenterprise", ForgeKind.GITHUB),
                ("bitbucket", ForgeKind.BITBUCKET),
            ),
        ),
        merge_subject_res=_SUBJECT_REF_RES,
    )
)
