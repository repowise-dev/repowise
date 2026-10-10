"""GitHub (github.com and Enterprise Server)."""

from __future__ import annotations

from dataclasses import dataclass

from .base import (
    BaseForge,
    ForgeKind,
    RemoteParts,
    RemoteRef,
    join_url,
    quote_path,
    quote_segment,
)
from .registry import register

PUBLIC_HOST = "github.com"
_PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
# ssh.github.com serves ssh over port 443; both aliases are github.com.
_HOST_ALIASES = frozenset({PUBLIC_HOST, "www.github.com", "ssh.github.com"})


@dataclass(frozen=True, slots=True)
class GitHub(BaseForge):
    def claims_host(self, host: str) -> bool:
        return host in _HOST_ALIASES

    def parse(self, parts: RemoteParts) -> RemoteRef | None:
        if len(parts.segments) < 2:
            return None
        owner, repo = parts.segments[0], parts.segments[1]
        public = parts.host in _HOST_ALIASES
        host = PUBLIC_HOST if public else parts.host
        origin = _PUBLIC_ORIGIN if public else parts.web_origin
        return RemoteRef(
            self.kind, host, (owner,), repo, join_url(origin, owner, repo), not public
        )

    def commit_url(self, ref: RemoteRef, sha: str) -> str:
        return f"{ref.web_base}/commit/{quote_segment(sha)}"

    def change_url(self, ref: RemoteRef, number: int) -> str:
        return f"{ref.web_base}/pull/{number}"

    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str:
        anchor = f"#L{line}" if line else ""
        return f"{ref.web_base}/blob/{quote_path(rev)}/{quote_path(path)}{anchor}"

    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str:
        return f"{ref.web_base}/compare/{quote_path(base)}...{quote_path(head)}"


register(
    GitHub(
        kind=ForgeKind.GITHUB,
        label="GitHub",
        ci_env_markers=("GITHUB_ACTIONS",),
    )
)
