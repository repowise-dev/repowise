"""GitLab (gitlab.com and self-managed)."""

from __future__ import annotations

from dataclasses import dataclass

from .base import (
    BaseForge,
    CiSystem,
    ForgeKind,
    RemoteParts,
    RemoteRef,
    join_url,
    quote_path,
    quote_segment,
)
from .registry import register

PUBLIC_HOST = "gitlab.com"


@dataclass(frozen=True, slots=True)
class GitLab(BaseForge):
    def claims_host(self, host: str) -> bool:
        # gitlab.com, and a self-managed host named for it (gitlab.corp.com).
        return "gitlab" in host.split(".")

    def parse(self, parts: RemoteParts) -> RemoteRef | None:
        segs = parts.segments
        if len(segs) < 2:
            return None
        return RemoteRef(
            self.kind,
            parts.host,
            segs[:-1],
            segs[-1],
            join_url(parts.web_origin, *segs),
            parts.host != PUBLIC_HOST,
        )

    def commit_url(self, ref: RemoteRef, sha: str) -> str:
        return f"{ref.web_base}/-/commit/{quote_segment(sha)}"

    def change_url(self, ref: RemoteRef, number: int) -> str:
        return f"{ref.web_base}/-/merge_requests/{number}"

    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str:
        anchor = f"#L{line}" if line else ""
        return f"{ref.web_base}/-/blob/{quote_path(rev)}/{quote_path(path)}{anchor}"

    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str:
        return f"{ref.web_base}/-/compare/{quote_path(base)}...{quote_path(head)}"

    def format_change_ref(self, number: int) -> str:
        return f"!{number}"


register(
    GitLab(
        kind=ForgeKind.GITLAB,
        label="GitLab",
        change_term="merge request",
        ci=CiSystem(
            name="gitlab_ci",
            markers=("GITLAB_CI",),
            base_branch=("CI_MERGE_REQUEST_TARGET_BRANCH_NAME",),
            # The merge base, so a merged-results pipeline still diffs only the MR.
            base_sha=("CI_MERGE_REQUEST_DIFF_BASE_SHA",),
            head_sha=("CI_COMMIT_SHA",),
            change_number=("CI_MERGE_REQUEST_IID",),
            repo_url=(("CI_PROJECT_URL",),),
        ),
        route_markers=frozenset({"-"}),
    )
)
