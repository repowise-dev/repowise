"""The ``issue-source`` contract, its key helpers, and the fix walk it feeds.

A plugin author can run the contract checks against their own source: build an
:class:`IssueRequest` from a few commits and pass the source to
:func:`run_issue_fetch` with a store that records what it is handed.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexTier
from repowise.core.ingestion.git_indexer.fix_walk_cache import FixWalkCache
from repowise.core.ingestion.git_indexer.prior_defects import (
    collect_fix_commits,
    compute_prior_defects,
)
from repowise.core.issues import (
    IssueCommit,
    IssueLink,
    IssueRequest,
    SourceRefused,
    SourceUnavailable,
    number_refs,
    resolve_links,
    run_issue_fetch,
    tracker_keys,
)

_REPO = Path(__file__).resolve().parents[3]
A, B, C = "a" * 40, "b" * 40, "c" * 40
_COMMITS = (
    IssueCommit(A, "Handle empty carts (ENG-42)"),
    IssueCommit(B, "Squash: retry the upload (#7)", change=7),
    IssueCommit(C, "Merge the retry (#7)", change=7),
)


def test_key_helpers_read_the_common_formats() -> None:
    text = (
        "ENG-42 and [OPS-7]: UTF-8, SHA-256, CVE-2024-1234, eng-3; closes #12, see #13, a#5 &#38;"
    )
    assert tracker_keys(text) == ["ENG-42", "OPS-7"]
    assert tracker_keys(text, projects=["ops"]) == ["OPS-7"]
    assert number_refs(text) == [12, 13]


def test_a_change_number_links_every_commit_that_names_it() -> None:
    links = resolve_links([IssueLink(key="#40", type="Bug", change=7)], _COMMITS)
    assert [(link.sha, link.type) for link in links] == [(B, "bug"), (C, "bug")]
    assert resolve_links([IssueLink(key="#41", type="bug", change=99)], _COMMITS) == []


@pytest.mark.parametrize(
    ("link", "message"),
    [
        (IssueLink(key="ENG-1", type="defect", sha=A), "type 'defect'"),
        (IssueLink(key="ENG-1", type="bug", sha="d" * 40), "was not asked about"),
        (IssueLink(key="ENG-1", type="bug"), "exactly one of sha and change"),
        (IssueLink(key="ENG-1", type="bug", sha=A, change=7), "exactly one of sha and change"),
        (IssueLink(key="", type="bug", sha=A), "1 to 64 characters"),
    ],
)
def test_a_link_that_breaks_the_contract_is_refused(link: IssueLink, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        resolve_links([link], _COMMITS)


class _Source:
    name = "t"

    def __init__(self, links, error: Exception | None = None) -> None:
        self._links, self._error = links, error

    def fetch(self, request):
        yield from self._links
        if self._error is not None:
            raise self._error


def _fetch(source, request=None):
    handed: list = []

    async def store(links, complete):
        handed.append((links, complete))
        return {"stored": len(links)}

    outcome = asyncio.run(
        run_issue_fetch(source, request or IssueRequest("r", commits=_COMMITS), store)
    )
    return outcome, handed


def test_a_complete_fetch_hands_every_link_over_as_complete() -> None:
    outcome, handed = _fetch(_Source([IssueLink(key="ENG-42", type="bug", sha=A)]))
    assert (outcome.status, outcome.error, outcome.cursor) == ("ok", None, None)
    assert [(len(links), complete) for links, complete in handed] == [(1, True)]


@pytest.mark.parametrize(
    ("error", "status", "message"),
    [
        (
            SourceUnavailable("rate limited, retry in 60 s"),
            "unavailable",
            "rate limited, retry in 60 s",
        ),
        (SourceRefused("the token lacks read:issues"), "refused", "the token lacks read:issues"),
        (RuntimeError("boom"), "failed", "RuntimeError: boom"),
    ],
)
def test_a_failed_fetch_keeps_what_it_read_and_drops_nothing(error, status, message) -> None:
    outcome, handed = _fetch(_Source([IssueLink(key="ENG-42", type="bug", sha=A)], error))
    assert (outcome.status, outcome.error) == (status, message)
    # The link read before the failure is stored, as a partial fetch that removes nothing.
    assert [(len(links), complete) for links, complete in handed] == [(1, False)]


def test_a_bad_link_fails_the_fetch_and_keeps_the_good_ones() -> None:
    good = IssueLink(key="ENG-42", type="bug", sha=A)
    outcome, handed = _fetch(_Source([good, IssueLink(key="ENG-9", type="epic", sha=A)]))
    assert outcome.status == "failed"
    assert "type 'epic'" in outcome.error
    assert [[link.key for link in links] for links, _ in handed] == [["ENG-42"]]


def test_credentials_stay_out_of_a_printed_request() -> None:
    request = IssueRequest("r", commits=_COMMITS, credentials="tok-secret")
    assert "tok-secret" not in repr(request)


def test_the_documented_minimal_source_passes_the_contract(monkeypatch) -> None:
    """The copy-paste example in docs/reference/PLUGINS.md runs as written."""
    doc = (_REPO / "docs" / "reference" / "PLUGINS.md").read_text(encoding="utf-8")
    section = doc.split("### Writing an issue source", 1)[1]
    code = section.split("```python", 1)[1].split("```", 1)[0]

    import io
    import json
    import urllib.request

    asked: list[str] = []

    def urlopen(req, timeout=None):
        asked.append(req.full_url)
        assert req.get_header("Authorization") == "Bearer tok"
        issue = {"type": "Bug", "status": "Done", "title": "Empty carts"}
        return io.BytesIO(json.dumps(issue).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    namespace: dict = {}
    exec(compile(code, "PLUGINS.md", "exec"), namespace)
    source = namespace["TrackerIssues"]()
    url, token = namespace["URL"], namespace["TOKEN"]
    assert source.settings == (url, token)
    request = IssueRequest(
        "r",
        commits=_COMMITS,
        settings={url.key: "https://tracker.example"},
        credentials={token.key: "tok"},
    )
    outcome, handed = _fetch(source, request)

    assert outcome.status == "ok", outcome.error
    [(links, complete)] = handed
    assert complete is True
    assert [(link.sha, link.key, link.type, link.basis) for link in links] == [
        (A, "ENG-42", "bug", "message names ENG-42")
    ]
    assert asked == ["https://tracker.example/api/issue/ENG-42"]


# ---------------------------------------------------------------------------
# The fix walk
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout.strip()


def _history(tmp_path: Path) -> tuple[Path, str]:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "add a")
    (tmp_path / "a.py").write_text("def f():\n    return 2\n")
    _git(tmp_path, "commit", "-am", "Handle empty carts (ENG-42)")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _repo(path: Path):
    from repowise.core.git_repo import Repo

    return Repo(path)


def test_a_tracker_fix_counts_and_nothing_else_moves(tmp_path: Path) -> None:
    path, linked = _history(tmp_path)
    repo = _repo(path)
    plain = compute_prior_defects(repo, {"a.py"}, as_of_ts=None)
    assert plain.counts == {}
    assert compute_prior_defects(repo, {"a.py"}, as_of_ts=None, tracker_fixes=frozenset()) == plain

    walk = collect_fix_commits(repo, {"a.py"}, as_of_ts=None, tracker_fixes=frozenset({linked}))
    assert [(f.sha, f.subject, f.shape_kind) for f in walk.fixes] == [
        (linked, "Handle empty carts (ENG-42)", "code_fix")
    ]
    assert compute_prior_defects(
        repo, {"a.py"}, as_of_ts=None, tracker_fixes=frozenset({linked})
    ).counts == {"a.py": 1}


def test_the_walk_cache_rereads_when_the_linked_commits_change(tmp_path: Path) -> None:
    path, linked = _history(tmp_path)
    (path / ".repowise").mkdir()
    repo = _repo(path)
    no_links = FixWalkCache.load(path / ".repowise", repo)
    assert "issues" not in no_links._key  # the key a cache written before links had
    compute_prior_defects(repo, {"a.py"}, as_of_ts=None, walk_cache=no_links)

    linked_cache = FixWalkCache.load(path / ".repowise", repo, tracker_fixes=frozenset({linked}))
    assert linked_cache.records == {}  # a different key: nothing reused
    counts = compute_prior_defects(
        repo,
        {"a.py"},
        as_of_ts=None,
        walk_cache=linked_cache,
        tracker_fixes=frozenset({linked}),
    ).counts
    assert counts == {"a.py": 1}
    again = FixWalkCache.load(path / ".repowise", repo, tracker_fixes=frozenset({linked}))
    assert again.records  # the same links reuse the records just written


@pytest.mark.asyncio
async def test_an_indexer_without_links_writes_what_it_always_did(tmp_path: Path) -> None:
    path, _ = _history(tmp_path)
    before = await GitIndexer(path, tier=GitIndexTier.FULL).index_repo("r")
    after = await GitIndexer(path, tier=GitIndexTier.FULL, tracker_fixes=frozenset()).index_repo(
        "r"
    )
    assert before[1] == after[1]
    assert before[0].fix_event_rows == after[0].fix_event_rows


def test_a_token_an_http_error_quotes_never_reaches_the_outcome() -> None:
    request = IssueRequest("r", commits=_COMMITS, credentials="tok-secret")
    outcome, _ = _fetch(_Source([], RuntimeError("GET /issue?token=tok-secret: 500")), request)
    assert outcome.status == "failed"
    assert "tok-secret" not in outcome.error


def test_the_first_bad_link_stops_the_fetch() -> None:
    good = IssueLink(key="ENG-42", type="bug", sha=A)
    later = IssueLink(key="ENG-43", type="bug", sha=A)
    outcome, handed = _fetch(_Source([good, None, later]))
    assert outcome.status == "failed"
    assert "got NoneType" in outcome.error
    assert [[link.key for link in links] for links, _ in handed] == [["ENG-42"]]


def test_a_secret_in_a_refusal_is_masked() -> None:
    request = IssueRequest("r", commits=_COMMITS, credentials={"tok": "tok-secret"})
    outcome, _ = _fetch(_Source([], SourceRefused("token tok-secret was revoked")), request)
    assert outcome.error == "token *** was revoked"
