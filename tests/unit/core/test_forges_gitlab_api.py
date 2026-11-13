"""The GitLab notes client and ``ci-summary --gitlab-note``, replayed against
exchanges recorded from gitlab.com (identities anonymised, no headers kept).

The recorded runs, in order, on one merge request: the first pipeline posts
the note; the second finds it on page two of the notes and edits it; a push
adding a security finding edits it to fail; reverting the push edits it again,
counting the finding as resolved.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from repowise.cli.commands.ci_summary_cmd import ci_summary_command, post_gitlab_note
from repowise.core.ci.summary import GateSummary, with_exit_code
from repowise.core.forges import gitlab_api
from repowise.core.forges.gitlab_api import (
    GitLabClient,
    GitLabError,
    find_note,
    project_path,
    upsert_note,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "gitlab_api" / "notes.json"
RECORDED = json.loads(FIXTURE.read_text(encoding="utf-8"))
API = "https://gitlab.com/api/v4"
ENV = {
    "CI_API_V4_URL": API,
    "CI_PROJECT_ID": "88814111",
    "CI_MERGE_REQUEST_IID": "1",
    "REPOWISE_GITLAB_TOKEN": "test-token",
}
GATES = ("doc-drift", "security", "health")


class Replay(httpx.BaseTransport):
    """Answers each request with the next recorded exchange, after checking the
    request is the one that was recorded (method, URL and JSON body)."""

    def __init__(self, exchanges: list[dict]) -> None:
        self.exchanges = list(exchanges)
        self.seen: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert self.exchanges, f"unexpected request {request.method} {request.url}"
        ex = self.exchanges.pop(0)
        assert (request.method, str(request.url)) == (ex["method"], ex["url"])
        sent = json.loads(request.content) if request.content else None
        assert sent == ex["request_json"]
        assert request.headers["PRIVATE-TOKEN"] == "test-token"
        self.seen.append(request)
        headers = {"Link": ex["link"]} if ex["link"] else {}
        return httpx.Response(ex["status"], json=ex["json"], headers=headers)


def _rows(key: str = "rows", codes: dict[str, int] | None = None) -> list[GateSummary]:
    codes = codes or {}
    return [
        with_exit_code(GateSummary.from_dict(RECORDED[key][g]), g, codes.get(g, 0))
        for g in GATES
    ]


def _post(scenario: str, rows: list[GateSummary], capsys, env=None) -> tuple[Replay, str]:
    replay = Replay(RECORDED[scenario])
    post_gitlab_note(rows, env={**ENV, **(env or {})}, transport=replay)
    assert not replay.exchanges, "a recorded request was never made"
    return replay, capsys.readouterr().err.strip()


def test_the_first_pipeline_posts_one_note(capsys, monkeypatch) -> None:
    monkeypatch.setattr(gitlab_api, "PER_PAGE", 1)  # as recorded
    replay, said = _post("first", _rows(), capsys)
    assert said == "Posted merge-request note 4213537997."
    body = json.loads(replay.seen[-1].content)["body"]
    assert body.startswith("<!-- repowise-ci-summary -->\n### Repowise gates: pass")


def test_a_later_pipeline_finds_its_note_across_pages_and_edits_it(capsys, monkeypatch) -> None:
    monkeypatch.setattr(gitlab_api, "PER_PAGE", 1)  # as recorded, to page through the notes
    replay, said = _post("second", _rows(), capsys)
    assert said == "Updated merge-request note 4213537997."
    # The second page came from the Link header's URL, not a rebuilt query.
    assert "page=2" in str(replay.seen[2].url)
    assert replay.seen[-1].method == "PUT"


def test_a_failing_gate_lists_its_finding_in_the_note(capsys) -> None:
    replay, said = _post("finding", _rows("finding_rows", {"security": 1}), capsys)
    assert said == "Updated merge-request note 4213537997."
    body = json.loads(replay.seen[-1].content)["body"]
    assert "### Repowise gates: fail" in body
    assert "eval_call (high)" in body and "repowise-fingerprint:v1:security:" in body


def test_a_finding_fixed_since_the_last_pipeline_counts_as_resolved(capsys) -> None:
    replay, said = _post("resolved", _rows(), capsys)
    assert said == "Updated merge-request note 4213537997."
    body = json.loads(replay.seen[-1].content)["body"]
    assert "Resolved since the last run: 1 finding." in body


@pytest.mark.parametrize(
    ("scenario", "env", "expected"),
    [
        ("bad_token", {}, "rejected the token (401)"),
        ("no_mr", {"CI_MERGE_REQUEST_IID": "999"}, "found nothing at projects/88814111"),
    ],
)
def test_a_refused_request_says_what_to_check_and_never_raises(
    capsys, scenario, env, expected
) -> None:
    _, said = _post(scenario, _rows(), capsys, env)
    assert said.startswith("Could not post the merge-request note.")
    assert expected in said


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"CI_MERGE_REQUEST_IID": ""}, "Not a merge-request pipeline"),
        ({"REPOWISE_GITLAB_TOKEN": ""}, "REPOWISE_GITLAB_TOKEN, CI_API_V4_URL and CI_PROJECT_ID"),
        ({"CI_API_V4_URL": ""}, "REPOWISE_GITLAB_TOKEN, CI_API_V4_URL and CI_PROJECT_ID"),
    ],
)
def test_without_a_merge_request_or_a_token_nothing_is_sent(capsys, env, expected) -> None:
    replay = Replay([])
    post_gitlab_note(_rows(), env={**ENV, **env}, transport=replay)
    said = capsys.readouterr().err.strip()
    assert expected in said and "\n" not in said
    assert not replay.seen


# -- the client on its own ----------------------------------------------------


def _client(handler) -> GitLabClient:
    return GitLabClient(API, "test-token", transport=httpx.MockTransport(handler))


def _note(note_id: int, body: str, author: int, system: bool = False) -> dict:
    return {"id": note_id, "body": body, "author": {"id": author}, "system": system}


def test_only_the_tokens_own_note_counts() -> None:
    marker = "<!-- repowise-ci-summary -->"
    notes = [
        _note(1, f"{marker}\nposted by someone else", author=7),
        _note(2, f"{marker}\nthe system", author=1001, system=True),
        _note(3, "a reply quoting nothing", author=1001),
        _note(4, f"{marker}\nours", author=1001),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 1001})
        return httpx.Response(200, json=notes)

    with _client(handler) as client:
        assert find_note(client, 88814111, 1, marker)["id"] == 4
        assert find_note(client, 88814111, 1, "<!-- repowise-ci-summary:sub -->") is None


def test_a_note_deleted_since_it_was_found_is_posted_again() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        if request.method == "PUT":
            return httpx.Response(404, json={"message": "404 Not found"})
        return httpx.Response(201, json={"id": 9})

    with _client(handler) as client:
        note, created = upsert_note(client, 88814111, 1, "body", existing={"id": 4})
    assert (note["id"], created, calls) == (9, True, ["PUT", "POST"])


def test_an_edit_refused_for_another_reason_is_not_reposted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        return httpx.Response(403, json={"message": "403 Forbidden"})

    with _client(handler) as client, pytest.raises(GitLabError) as exc:
        upsert_note(client, 88814111, 1, "body", existing={"id": 4})
    assert exc.value.status == 403 and "Reporter role" in str(exc.value)


def test_a_next_page_on_another_host_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        link = '<https://evil.example/api/v4/next?page=2>; rel="next"'
        return httpx.Response(200, json=[{"id": 1}], headers={"Link": link})

    with _client(handler) as client, pytest.raises(GitLabError, match="another host"):
        list(client.paginate("projects/1/merge_requests/1/notes"))


def test_an_unexpected_status_carries_gitlabs_own_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": {"note": ["is too long {x}"]}})

    with _client(handler) as client, pytest.raises(GitLabError) as exc:
        client.request("POST", "projects/1/merge_requests/1/notes", json={"body": "x"})
    assert exc.value.status == 422
    assert "GitLab answered 422 to POST projects/1/merge_requests/1/notes" in str(exc.value)
    assert "is too long {x}" in str(exc.value)


def test_an_unreachable_host_is_an_error_without_a_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with _client(handler) as client, pytest.raises(GitLabError) as exc:
        client.request("GET", "user")
    assert exc.value.status is None and "Could not reach GitLab at" in str(exc.value)


def test_an_answer_in_the_wrong_shape_never_raises(capsys) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 1001})
        if request.method == "GET":
            return httpx.Response(200, json={"not": "a list"})
        return httpx.Response(201, json=["no id"])

    post_gitlab_note(_rows(), env=ENV, transport=httpx.MockTransport(handler))
    assert "was not a list" in capsys.readouterr().err

    def no_id(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"id": 1001})
        return httpx.Response(200 if request.method == "GET" else 201, json=[] if request.method == "GET" else ["x"])

    post_gitlab_note(_rows(), env=ENV, transport=httpx.MockTransport(no_id))
    assert "unexpected answer" in capsys.readouterr().err


def test_a_web_page_instead_of_the_api_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>sign in</html>")

    with _client(handler) as client, pytest.raises(GitLabError, match="was not JSON"):
        client.request("GET", "user")


def test_a_self_managed_api_root_and_a_project_path_both_work() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"id": 1})

    client = GitLabClient("https://git.corp.example/api/v4/", "t", transport=httpx.MockTransport(handler))
    client.request("GET", project_path("group/sub group/app"))
    assert seen == ["https://git.corp.example/api/v4/projects/group%2Fsub%20group%2Fapp"]


# -- the command --------------------------------------------------------------


def test_outside_a_merge_request_the_command_still_prints_the_verdict(tmp_path) -> None:
    env = {k: "" for k in ENV} | {"GITHUB_STEP_SUMMARY": ""}
    # Click 8.2 always keeps stderr apart and dropped the argument.
    split = "mix_stderr" in inspect.signature(CliRunner.__init__).parameters
    runner = CliRunner(mix_stderr=False) if split else CliRunner()
    result = runner.invoke(
        ci_summary_command, [str(tmp_path), "--gate", "security=0", "--gitlab-note"], env=env
    )
    assert result.exit_code == 0
    assert result.stdout.strip() == "warn"  # exited 0 but left no row
    assert result.stderr.strip() == "Not a merge-request pipeline, so no merge-request note is posted."
