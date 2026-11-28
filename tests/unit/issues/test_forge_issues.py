"""The built-in ``forge`` issue source, against recorded GitHub and GitLab answers.

The fixtures were recorded from biomejs/biome (GitHub, which uses issue
types) and gitlab-org/cli (GitLab, which uses ``type::bug`` labels), with
two made-up commits each for message mentions and reverts. No test here
touches the network: an unrecorded request fails the test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from repowise.core.forges.remote import parse_remote
from repowise.core.issues import (
    IssueCommit,
    IssueRequest,
    SourceRefused,
    issue_sources,
    run_issue_fetch,
)
from repowise.core.issues.forge import ForgeIssues, closing_mentions, github, gitlab, link_type
from repowise.core.issues.forge.http import retry_point
from repowise.core.settings.areas.plugins import FORGE_API_URL, FORGE_BUG_LABELS, FORGE_TOKEN

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "ghp_forge-sentinel-7c2d"
GITHUB = parse_remote("https://github.com/biomejs/biome.git")
GITLAB = parse_remote("https://gitlab.com/gitlab-org/cli.git")


def _replay(name: str) -> tuple[list[IssueCommit], httpx.MockTransport, list[str]]:
    """The fixture's commits and a transport that answers only its recorded requests."""
    doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    answers = {(e["method"], e["url"], json.dumps(e["body"])): e for e in doc["exchanges"]}
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        entry = answers.get((request.method, str(request.url), json.dumps(body)))
        assert entry is not None, f"unrecorded request {request.method} {request.url}"
        assert (
            request.headers.get("authorization", request.headers.get("private-token")) is not None
        )
        seen.append(str(request.url))
        return httpx.Response(entry["status"], json=entry["json"])

    commits = [IssueCommit(**c) for c in doc["commits"]]
    return commits, httpx.MockTransport(handle), seen


def _fetch(commits, transport, remote, **settings):
    request = IssueRequest(
        "r",
        commits=commits,
        remote=remote,
        settings=settings,
        credentials={FORGE_TOKEN.key: TOKEN},
    )
    return list(ForgeIssues(transport=transport).fetch(request))


def _rows(links):
    return [(link.sha[:10], link.key, link.type, link.status, link.basis) for link in links]


def test_github_links_come_from_closing_references_and_closing_mentions() -> None:
    commits, transport, seen = _replay("forge_github.json")
    links = _fetch(commits, transport, GITHUB)
    assert _rows(links) == [
        # The message closes a Bug; "closes #13491" names a pull request, so it is skipped.
        ("3434343434", "#12957", "bug", "closed", "commit message closes #12957"),
        # PR #13491 closes a Bug-typed issue: a defect. The newer "release" commit
        # that closes it again is dropped: an issue keeps its first fix.
        ("cc402faf81", "#13428", "bug", "closed", "PR #13491 closes #13428"),
        # A Task-typed issue stays a task, whatever its labels.
        ("71660779e6", "#5172", "task", "closed", "PR #12865 closes #5172"),
    ]
    assert links[1].url == "https://github.com/biomejs/biome/issues/13428"
    assert links[1].title.startswith("noUntrustedLicenses reports valid SPDX")
    # Three batched queries for nine commits: pull requests, closing issues, mentions.
    assert len(seen) == 3


def test_gitlab_links_read_merge_requests_and_bug_labels() -> None:
    commits, transport, _ = _replay("forge_gitlab.json")
    links = _fetch(commits, transport, GITLAB)
    assert _rows(links) == [
        # #8811 is not readable, so it is left out; the revert closes nothing.
        ("ababababab", "#8795", "bug", "closed", "commit message closes #8795"),
        # A plain issue with the type::bug label. The newer commit closing it again is dropped.
        ("5495c1cd9b", "#8705", "bug", "closed", "MR !4566 closes #8705"),
        ("0cbbacce12", "#8154", "other", "closed", "MR !4527 closes #8154"),
        ("2f2d807d60", "#7760", "other", "closed", "MR !4519 closes #7760"),
    ]
    assert links[1].url.startswith("https://gitlab.com/gitlab-org/cli/-/")


def test_a_label_set_decides_only_for_an_issue_with_no_type() -> None:
    commits, transport, _ = _replay("forge_gitlab.json")
    # The setting replaces the default set: type::bug no longer counts.
    links = _fetch(commits, transport, GITLAB, **{FORGE_BUG_LABELS.key: ["Backend"]})
    assert [link.type for link in links] == ["other", "other", "bug", "other"]
    commits, transport, _ = _replay("forge_github.json")
    # On GitHub the issue type wins over any label.
    links = _fetch(commits, transport, GITHUB, **{FORGE_BUG_LABELS.key: ["S-Enhancement"]})
    assert [link.type for link in links] == ["bug", "bug", "task"]


@pytest.mark.parametrize(
    ("native", "labels", "expected"),
    [
        ("Bug", [], "bug"),
        ("incident", ["bug"], "incident"),
        ("Feature", ["bug"], "feature"),
        ("Task", [], "task"),
        ("Epic", ["Type: Bug"], "bug"),
        ("issue", ["INCIDENT"], "incident"),
        (None, ["bug-report", "needs-triage"], "other"),
        (None, [], "other"),
    ],
)
def test_link_type_is_precision_first(native, labels, expected) -> None:
    assert link_type(native, labels, FORGE_BUG_LABELS.default) == expected


def test_an_issue_keeps_its_first_fix_and_a_rollup_closes_nothing() -> None:
    from repowise.core.issues import IssueLink
    from repowise.core.issues.forge import ROLLUP, first_closers

    def link(sha: str, key: str) -> IssueLink:
        return IssueLink(key=key, type="bug", sha=sha)

    # Newest first: a release, then the two commits of the PR that fixed #7.
    kept = first_closers(
        [("PR #9", link("c", "#7")), ("PR #4", link("b", "#7")), ("PR #4", link("a", "#7"))]
    )
    assert [(k.sha, k.key) for k in kept] == [("b", "#7"), ("a", "#7")]
    # A mention on one of the PR's commits is that PR's change, not a newer one.
    kept = first_closers([("PR #4", link("b", "#7")), ("PR #4", link("a", "#7"))])
    assert len(kept) == 2

    many = [{"iid": n, "issue_type": "issue", "labels": ["bug"]} for n in range(ROLLUP)]

    class Http:
        def send(self, method, url, params=None, json=None):
            return many

    api = gitlab._Api(Http(), "https://gitlab.com/api/v4", "g/p")
    assert api.closes_issues(1) == []
    many.pop()
    assert len(api.closes_issues(1)) == ROLLUP - 1


def test_closing_mentions_skip_reverts_bare_numbers_and_the_change_itself() -> None:
    def mentions(subject: str, body: str = "", change: int | None = None) -> list[int]:
        return closing_mentions(IssueCommit("a" * 40, subject, body, change), github.CLOSING_RE)

    assert mentions("Fix the parser", "Fixes #12, closes: #13\nresolved #12") == [12, 13]
    assert mentions("Handle #12 and see #14") == []
    assert mentions("fix: x (#40)", "Fixes #40", change=40) == []
    assert mentions('Revert "fix: x, fixes #12"') == []
    assert mentions("Fixes org/repo#12") == []
    assert closing_mentions(IssueCommit("a" * 40, "Implements #9"), gitlab.CLOSING_RE) == [9]


def test_api_urls_follow_the_remote_and_self_managed_hosts() -> None:
    ghe = parse_remote("git@ghe.corp.com:team/app.git", hosts={"ghe.corp.com": "github"})
    lab = parse_remote("https://git.corp.com/group/sub/app.git", hosts={"git.corp.com": "gitlab"})
    assert github.api_url(GITHUB) == "https://api.github.com/graphql"
    assert github.api_url(ghe) == "https://ghe.corp.com/api/graphql"
    assert gitlab.api_url(GITLAB) == "https://gitlab.com/api/v4"
    assert gitlab.api_url(lab) == "https://git.corp.com/api/v4"


def test_the_api_url_setting_wins_on_the_remote_host_only() -> None:
    hosts: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(200, json={"id": 1} if request.url.path.endswith("cli") else [])

    def request(url: str) -> IssueRequest:
        return IssueRequest(
            "r",
            commits=[IssueCommit("b" * 40, "docs: x")],
            remote=GITLAB,
            settings={FORGE_API_URL.key: url},
            credentials={FORGE_TOKEN.key: TOKEN},
        )

    source = ForgeIssues(transport=httpx.MockTransport(handle))
    assert list(source.fetch(request("https://gitlab.com/api/v4/"))) == []
    assert set(hosts) == {"gitlab.com"}
    # The key can come from a repository's own config, so the token never
    # goes to another host or over plain http.
    for url in ("https://collector.example.com/api/v4", "http://gitlab.com/api/v4"):
        with pytest.raises(SourceRefused, match=r"must be an https URL on gitlab.com"):
            list(source.fetch(request(url)))
    assert set(hosts) == {"gitlab.com"}


def _outcome(handler, remote=GITHUB):
    request = IssueRequest(
        "r",
        commits=[IssueCommit("c" * 40, "fix: x")],
        remote=remote,
        credentials={FORGE_TOKEN.key: TOKEN},
    )
    source = ForgeIssues(transport=httpx.MockTransport(handler))

    async def store(links, complete):
        return {"links": len(links)}

    return asyncio.run(run_issue_fetch(source, request, store))


def test_a_used_up_rate_limit_is_unavailable_with_its_reset_time() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1795856534"}
        )

    outcome = _outcome(handle)
    assert outcome.status == "unavailable"
    assert outcome.error == "GitHub's rate limit is used up; sync again after 2026-11-28 09:02 UTC."


def test_a_graphql_rate_limit_error_is_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1795856534"},
            json={"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]},
        )

    outcome = _outcome(handle)
    assert (outcome.status, outcome.error) == (
        "unavailable",
        "GitHub's rate limit is used up; sync again after 2026-11-28 09:02 UTC.",
    )


@pytest.mark.parametrize(
    ("status", "state", "text"),
    [
        (401, "refused", "rejected the token (401)"),
        (403, "refused", "needs read access"),
        (502, "unavailable", "answered 502"),
    ],
)
def test_http_failures_map_to_the_seam_rules_without_the_token(status, state, text) -> None:
    outcome = _outcome(lambda request: httpx.Response(status, text=f"bad {TOKEN}"), GITLAB)
    assert outcome.status == state
    assert text in outcome.error
    assert TOKEN not in outcome.error


def test_an_unreachable_host_is_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no route to {request.url}", request=request)

    outcome = _outcome(handle)
    assert (outcome.status, outcome.error) == (
        "unavailable",
        "GitHub did not answer (ConnectError); sync again later.",
    )


def test_an_unexplained_graphql_error_is_unavailable_and_stores_nothing() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"repository": {"c0": None}},
                "errors": [{"message": "Something went wrong", "path": ["repository", "c0"]}],
            },
        )

    outcome = _outcome(handle)
    assert (outcome.status, outcome.stored) == ("unavailable", [])
    assert outcome.error == "GitHub answered with an error (untyped); sync again later."


def test_a_repository_github_cannot_see_is_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {"repository": None},
                "errors": [{"type": "NOT_FOUND", "path": ["repository"], "message": "x"}],
            },
        )

    outcome = _outcome(handle)
    assert outcome.status == "refused"
    assert "biomejs/biome" in outcome.error


@pytest.mark.parametrize(
    ("remote", "text"),
    [
        (parse_remote("https://dev.azure.com/org/proj/_git/app"), "on Azure DevOps"),
        (None, "no origin or upstream remote"),
    ],
)
def test_a_remote_off_github_and_gitlab_is_refused_without_a_request(remote, text) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be sent")

    outcome = _outcome(handle, remote)
    assert outcome.status == "refused"
    assert text in outcome.error


def test_retry_after_wins_over_the_quota_reset() -> None:
    from datetime import UTC, datetime

    now = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
    headers = {"retry-after": "120", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "1"}
    assert retry_point(headers, now) == datetime(2026, 11, 28, 9, 2, tzinfo=UTC)
    assert retry_point({"ratelimit-remaining": "0", "ratelimit-reset": "1795856534"}, now)
    assert retry_point({"x-ratelimit-remaining": "12"}, now) is None


def test_it_is_registered_by_default_and_runs_only_with_a_token() -> None:
    from repowise.core.source_sync import plan_sources

    assert isinstance(issue_sources.get("forge"), ForgeIssues)
    (forge,) = [p for p in plan_sources({}, environ={}) if p.name == "forge"]
    assert not forge.configured
    assert [s.key for s in forge.missing] == ["REPOWISE_PLUGIN_FORGE_TOKEN"]
    (forge,) = [p for p in plan_sources({}, environ={FORGE_TOKEN.key: TOKEN}) if p.name == "forge"]
    assert forge.configured
    assert forge.credentials == {FORGE_TOKEN.key: TOKEN}
    assert forge.settings == {FORGE_BUG_LABELS.key: FORGE_BUG_LABELS.default}
    assert TOKEN not in repr(forge)


def test_with_no_token_an_update_sends_nothing_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from repowise.cli.commands.sources_cmd import sync_after_update

    def boom(*args, **kwargs):
        raise AssertionError("an unconfigured forge source must not run")

    monkeypatch.delenv(FORGE_TOKEN.key, raising=False)
    monkeypatch.setattr(ForgeIssues, "fetch", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    sync_after_update(tmp_path, before=True)
    sync_after_update(tmp_path)
    assert capsys.readouterr().out == ""
    assert list(tmp_path.iterdir()) == []


def test_the_request_carries_the_checkout_remote(tmp_path: Path) -> None:
    import subprocess

    from repowise.core.forges.base import ForgeKind
    from repowise.core.issues.sync import issue_request

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("remote", "add", "origin", "git@gitlab.com:group/sub/app.git")
    git(
        "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "fix: x"
    )
    request = issue_request(tmp_path, "r")
    assert request.remote is not None
    assert (request.remote.forge, request.remote.namespace, request.remote.repo) == (
        ForgeKind.GITLAB,
        ("group", "sub"),
        "app",
    )


def test_sources_sync_lists_the_forge_source_until_a_token_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.commands.sources_cmd import _planned

    monkeypatch.delenv(FORGE_TOKEN.key, raising=False)
    forge = [p for p in _planned(tmp_path, "forge", "json")]
    assert [(p.seam.name, p.name, p.configured) for p in forge] == [
        ("issue-source", "forge", False)
    ]
