"""Opening a merge or pull request on the Review page: ``?change=mr:<n>`` / ``pr:<n>``.

The request is read from the ref its forge publishes (GitLab's
``refs/merge-requests/<n>/head``, GitHub's ``refs/pull/<n>/head``) in the
server's clone, and comes back pinned to the two commits read. No test talks
to a network: refs are written locally, and the one fetch reads a bare repo
on disk through ``url.<path>.insteadOf``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repowise.core.repo_config import save_repo_config
from tests.unit.server.conftest import create_test_repo


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Dev", "-c", "user.email=dev@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, body in files.items():
        (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", *files)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _repo_with_request(
    repo: Path, *, url: str, ref: str
) -> tuple[str, str]:
    """main, a request published under *ref* only (its branch is gone), and main moved on.

    Returns the request's ``(base, head)`` commits.
    """
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    base = _commit(repo, {"core.py": "x = 1\n", "app.py": "import core\n"}, "init")
    _git(repo, "checkout", "-q", "-b", "source")
    head = _commit(repo, {"core.py": "x = 2\n"}, "feat: tune the core")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "branch", "-q", "-D", "source")
    _git(repo, "update-ref", ref, head)
    # main moves after the request forked: its own edits are not the request's.
    _commit(repo, {"app.py": "import core\nprint(core.x)\n"}, "chore: unrelated")
    _git(repo, "remote", "add", "origin", url)
    return base, head


async def test_a_gitlab_merge_request_opens_pinned_with_gitlab_words(client, tmp_path):
    repo_dir = tmp_path / "test-repo"
    base, head = _repo_with_request(
        repo_dir, url="https://gitlab.com/group/project.git", ref="refs/merge-requests/7/head"
    )
    repo = await create_test_repo(client, tmp_path)
    url = f"/api/repos/{repo['id']}/blast-radius/change"

    resp = await client.post(url, json={"change_id": "mr:7"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    pinned = f"mr:7@{base}...{head}"
    assert body["change_id"] == pinned
    # Only what the request changed, not main's later commit.
    assert body["changed_files"] == ["core.py"]
    change = body["change"]
    assert (change["kind"], change["label"], change["change_term"]) == (
        "change_request",
        "!7",
        "merge request",
    )
    assert (change["id"], change["number"], change["base_sha"], change["head_sha"]) == (
        pinned,
        7,
        base,
        head,
    )
    assert (change["title"], change["author"], change["files_changed"]) == (
        "feat: tune the core",
        "Dev",
        1,
    )
    # The score and tests read the same two commits as the blast radius.
    assert body["review"] is not None, body["review_unavailable"]
    assert body["review"]["revspec"] == f"{base}...{head}"

    # The request moves on; the pinned link still names the diff it was shared with.
    _git(repo_dir, "checkout", "-q", head)
    newer = _commit(repo_dir, {"extra.py": "y = 1\n"}, "feat: more")
    _git(repo_dir, "checkout", "-q", "main")
    _git(repo_dir, "update-ref", "refs/merge-requests/7/head", newer)

    again = (await client.post(url, json={"change_id": pinned})).json()
    assert (again["change_id"], again["changed_files"]) == (pinned, ["core.py"])
    latest = (await client.post(url, json={"change_id": "mr:7"})).json()
    assert latest["change_id"] == f"mr:7@{base}...{newer}"
    assert latest["changed_files"] == ["core.py", "extra.py"]

    check = await client.post(
        f"/api/repos/{repo['id']}/blast-radius/health-check", json={"change_id": pinned}
    )
    assert check.status_code == 200, check.text
    assert check.json()["revspec"] == f"{base}...{head}"


async def test_a_github_pull_request_uses_github_words(client, tmp_path):
    base, head = _repo_with_request(
        tmp_path / "test-repo",
        url="https://github.com/owner/project.git",
        ref="refs/pull/3/head",
    )
    repo = await create_test_repo(client, tmp_path)

    resp = await client.post(
        f"/api/repos/{repo['id']}/blast-radius/change", json={"change_id": "pr:3"}
    )

    assert resp.status_code == 200, resp.text
    change = resp.json()["change"]
    assert (change["label"], change["change_term"], change["id"]) == (
        "#3",
        "pull request",
        f"pr:3@{base}...{head}",
    )


async def test_a_self_managed_gitlab_is_known_by_its_ref_shape(client, tmp_path):
    """A host no setting maps reads as plain git; the published ref still says GitLab."""
    _repo_with_request(
        tmp_path / "test-repo",
        url="https://git.corp.example/group/project.git",
        ref="refs/merge-requests/12/head",
    )
    repo = await create_test_repo(client, tmp_path)

    resp = await client.post(
        f"/api/repos/{repo['id']}/blast-radius/change", json={"change_id": "mr:12"}
    )

    assert resp.status_code == 200, resp.text
    assert (resp.json()["change"]["label"], resp.json()["change"]["change_term"]) == (
        "!12",
        "merge request",
    )


async def test_a_request_this_clone_lacks_is_not_fetched_into_a_developer_clone(
    client, tmp_path, monkeypatch
):
    from repowise.server.routers import blast_radius as route

    _repo_with_request(
        tmp_path / "test-repo",
        url="https://gitlab.com/group/project.git",
        ref="refs/merge-requests/7/head",
    )
    repo = await create_test_repo(client, tmp_path)

    def no_fetch(*_args):
        raise AssertionError("a developer's clone is never fetched")

    monkeypatch.setattr(route, "fetch_ref", no_fetch)
    url = f"/api/repos/{repo['id']}/blast-radius/change"
    missing = await client.post(url, json={"change_id": "mr:99"})
    assert missing.status_code == 404
    assert "merge request !99" in missing.json()["detail"]
    for bad in ("mr:0", "mr:7@abc...def", "mr:x", f"mr:7@{'a' * 40}...{'b' * 40}"):
        assert (await client.post(url, json={"change_id": bad})).status_code == 404, bad


async def test_a_managed_checkout_fetches_the_published_ref(client, tmp_path):
    """A clone the server owns fetches the one ref; its branch and working tree stay put."""
    seed = tmp_path / "seed"
    url = "https://gitlab.com/group/project.git"
    base, head = _repo_with_request(seed, url=url, ref="refs/merge-requests/5/head")
    bare = tmp_path / "remote.git"
    _git(tmp_path, "clone", "-q", "--mirror", str(seed), str(bare))

    checkout = tmp_path / "test-repo"
    _git(tmp_path, "clone", "-q", str(bare), str(checkout))
    _git(checkout, "remote", "set-url", "origin", url)
    # The forge URL stays in the config (it names the forge); git reads the bare repo.
    _git(checkout, "config", f"url.{bare.as_uri()}.insteadOf", url)
    save_repo_config(checkout, {"managed_checkout": True})
    main_before = _git(checkout, "rev-parse", "HEAD")
    assert _git(checkout, "for-each-ref", "refs/merge-requests") == ""
    repo = await create_test_repo(client, tmp_path)

    resp = await client.post(
        f"/api/repos/{repo['id']}/blast-radius/change", json={"change_id": "mr:5"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["change_id"] == f"mr:5@{base}...{head}"
    assert _git(checkout, "rev-parse", "refs/merge-requests/5/head") == head
    assert _git(checkout, "rev-parse", "HEAD") == main_before
