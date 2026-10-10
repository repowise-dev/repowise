"""GitLab (gitlab.com and self-managed)."""

from __future__ import annotations

import re
from dataclasses import dataclass

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

PUBLIC_HOST = "gitlab.com"

# ``See merge request group/proj!12`` closes a merge commit's body; ``!12``
# alone is GitLab's shorthand, never preceded by a word char or another ``!``.
# The ``!`` leads, ahead of the lookbehind, so the scan jumps from ``!`` to
# ``!`` instead of trying every position (about 17x faster on a real history).
# Newer GitLab writes the trailer's reference as the MR's full URL. The path
# is bounded so a run of trailers over long tokens stays linear.
_MERGE_TRAILER_RE = re.compile(
    r"See merge request \S{0,512}?(?:!|/-/merge_requests/)(\d{1,9})\b"
)
_SHORTHAND_RE = re.compile(r"!(?<![\w!]!)(\d{1,9})\b")
_URL_REF_RE = re.compile(r"/-/merge_requests/(\d{1,9})\b")
_REF_RES = (_MERGE_TRAILER_RE, _SHORTHAND_RE, _URL_REF_RE)
# Off GitLab, ``!12`` is as likely an exclamation as a reference: only the
# explicit trailer and links count there.
_FOREIGN_REF_RES = (_MERGE_TRAILER_RE, _URL_REF_RE)
# A squash subject closed with ``(!12)`` is that MR, as ``(#12)`` is on
# GitHub; a bare ``!12`` elsewhere in a subject only mentions one.
_NATIVE_MERGE_SUBJECT_RES = (re.compile(r"\(!(\d{1,9})\)\s*$"),)

# ``NNN-login@users.noreply.<host>``, on gitlab.com and self-managed hosts.
# GitLab always writes the id, so there is no variant to fold: the address is
# kept, since dropping the id could only merge two accounts that reused a
# login. A rename (same id, new login) stays split; folding by id would join it.
_NOREPLY_RE = re.compile(r"^\d+-(?P<login>[^@\s+]+)@users\.noreply\.(?!github\.com$)[^@\s]+$")
# Project and group access tokens commit as ``project_<id>_bot_<hex>`` at
# ``noreply.<host>``; ``Ghost User`` holds a deleted account's work.
_BOT_LOGIN = r"(?:project|group)_\d+_bot\w*"
_BOT_NAME_RE = re.compile(rf"^{_BOT_LOGIN}$|^ghost user$", re.IGNORECASE)
_BOT_EMAIL_RE = re.compile(rf"^{_BOT_LOGIN}@noreply\.", re.IGNORECASE)


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

    def parse_change_refs(self, subject: str, body: str = "", *, native: bool = True) -> list[int]:
        return collect_refs(subject, body, (), _REF_RES if native else _FOREIGN_REF_RES)


register(
    GitLab(
        kind=ForgeKind.GITLAB,
        label="GitLab",
        change_term="merge request",
        noreply_re=_NOREPLY_RE,
        bot_name_re=_BOT_NAME_RE,
        bot_email_re=_BOT_EMAIL_RE,
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
        merge_body_res=(_MERGE_TRAILER_RE,),
        merge_body_first=True,
        native_merge_subject_res=_NATIVE_MERGE_SUBJECT_RES,
        route_markers=frozenset({"-"}),
    )
)
