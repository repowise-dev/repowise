"""Bitbucket (Cloud at bitbucket.org, and Data Center / Server).

The two share a name and little else: Cloud is ``workspace/repo`` with
``/src`` and ``/commits`` pages; Data Center clones from ``/scm/PROJ/repo``
(ssh on its own port, usually 7999) and browses at ``/projects/PROJ/repos/repo``.
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
    quote_path,
    quote_segment,
)
from .registry import register

PUBLIC_HOST = "bitbucket.org"
_PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
_HOST_ALIASES = frozenset({PUBLIC_HOST, "www.bitbucket.org", "altssh.bitbucket.org"})

# Cloud merges write ``Merged in branch (pull request #12)``; Data Center
# writes ``Merge pull request #12 in PROJ/repo from branch to main``.
_SUBJECT_REF_RES = (
    re.compile(r"\(pull request #(\d{1,9})\)"),
    re.compile(r"^Merge pull request #(\d{1,9}) in "),
)
_URL_REF_RE = re.compile(r"/pull-requests/(\d{1,9})\b")
# Cloud's suffix closes the subject it merged; anywhere else it is quoted.
_MERGE_SUBJECT_RES = (
    re.compile(r"\(pull request #(\d{1,9})\)\s*$"),
    _SUBJECT_REF_RES[1],
)


def _data_center(parts: RemoteParts) -> tuple[tuple[str, ...], str, str] | None:
    """``(context path, project, repo)`` from a Data Center clone or browse URL."""
    segs = parts.segments
    if parts.scheme not in ("http", "https"):
        return ((), segs[0], segs[1]) if len(segs) >= 2 else None
    lowered = [s.lower() for s in segs]
    if "scm" in lowered:
        i = lowered.index("scm")
        return (segs[:i], segs[i + 1], segs[i + 2]) if i + 2 < len(segs) else None
    if "projects" in lowered:
        i = lowered.index("projects")
        if i + 3 < len(segs) and lowered[i + 2] == "repos":
            return segs[:i], segs[i + 1], segs[i + 3]
    return None


@dataclass(frozen=True, slots=True)
class Bitbucket(BaseForge):
    def claims_host(self, host: str) -> bool:
        return host in _HOST_ALIASES

    def parse(self, parts: RemoteParts) -> RemoteRef | None:
        segs = parts.segments
        if parts.host in _HOST_ALIASES:
            if len(segs) < 2:
                return None
            web_base = join_url(_PUBLIC_ORIGIN, segs[0], segs[1])
            return RemoteRef(self.kind, PUBLIC_HOST, (segs[0],), segs[1], web_base, False)
        found = _data_center(parts)
        if found is None:
            return None
        context, project, repo = found
        # Data Center project keys are uppercase; ssh remotes often spell them
        # lowercase, and both must name one project.
        project = project if project.startswith("~") else project.upper()
        # A personal repo's project is ``~user``; its pages live under /users.
        owner = ("users", project[1:]) if project.startswith("~") else ("projects", project)
        web_base = join_url(parts.web_origin, *context, *owner, "repos", repo)
        return RemoteRef(self.kind, parts.host, (project,), repo, web_base, True)

    def canonical_path(self, parts: RemoteParts) -> str | None:
        """Data Center keys as ``host/PROJ/repo``: its http clone (``/scm/``,
        maybe under a context path), ssh clone and browse URLs all match."""
        if parts.host in _HOST_ALIASES:
            return None
        ref = self.parse(parts)
        if ref is None:
            return None
        return "/".join([ref.host, *(quote_segment(s) for s in (*ref.namespace, ref.repo))])

    def commit_url(self, ref: RemoteRef, sha: str) -> str:
        return f"{ref.web_base}/commits/{quote_segment(sha)}"

    def change_url(self, ref: RemoteRef, number: int) -> str:
        suffix = "/overview" if ref.is_self_hosted else ""
        return f"{ref.web_base}/pull-requests/{number}{suffix}"

    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str:
        if ref.is_self_hosted:
            anchor = f"#{line}" if line else ""
            return f"{ref.web_base}/browse/{quote_path(path)}?at={quote(rev, safe='')}{anchor}"
        anchor = f"#lines-{line}" if line else ""
        return f"{ref.web_base}/src/{quote_path(rev)}/{quote_path(path)}{anchor}"

    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str:
        if ref.is_self_hosted:
            return (
                f"{ref.web_base}/compare/commits?sourceBranch={quote(head, safe='')}"
                f"&targetBranch={quote(base, safe='')}"
            )
        # Cloud joins the two revs with a CR, head first.
        return f"{ref.web_base}/branches/compare/{quote_path(head)}%0D{quote_path(base)}"

    def parse_change_refs(self, subject: str, body: str = "", *, native: bool = True) -> list[int]:
        return collect_refs(subject, body, _SUBJECT_REF_RES, (_URL_REF_RE,))


register(
    Bitbucket(
        kind=ForgeKind.BITBUCKET,
        label="Bitbucket",
        ci=CiSystem(
            name="bitbucket_pipelines",
            markers=("BITBUCKET_BUILD_NUMBER",),
            base_branch=("BITBUCKET_PR_DESTINATION_BRANCH",),
            head_sha=("BITBUCKET_COMMIT",),
            change_number=("BITBUCKET_PR_ID",),
            repo_url=(("BITBUCKET_GIT_HTTP_ORIGIN",),),
        ),
        merge_subject_res=_MERGE_SUBJECT_RES,
    )
)
