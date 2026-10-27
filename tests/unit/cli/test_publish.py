"""``repowise publish``: one command from a local repo to repowise.dev.

What protects the user here: only GitHub remotes are published, a signed-out
user is signed in with the publish source, every refusal the platform can
give reads as a next step with its link, and every link carries the source
(never the install id; only sign-in carries that).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner

from repowise.cli.platform import auth, credentials, links, store
from repowise.cli.platform import publish as pub
from repowise.cli.platform.client import PlatformClient


@pytest.fixture(autouse=True)
def isolated_platform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(credentials, "_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("REPOWISE_TELEMETRY_DISABLED", raising=False)
    store.update(anon_id="abc123def456")
    yield


def _sign_in() -> None:
    credentials.save(
        {
            "token_kind": "oauth",
            "client_id": "repowise-cli",
            "access_token": "at-1",
            "access_expires_at": int(time.time()) + 3600,
            "refresh_token": "rt-1",
        }
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "widget"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(
        path,
        "-c",
        "user.email=a@b.c",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "x",
    )
    _git(path, "remote", "add", "origin", "git@github.com:acme/widget.git")
    return path


def _answer(monkeypatch, status: int, body: dict, *, account: dict | None = None) -> list:
    sent: list = []

    def fake_post_json(self, path, payload, *, timeout=None):
        sent.append((path, payload))
        return status, body

    monkeypatch.setattr(PlatformClient, "post_json", fake_post_json)
    monkeypatch.setattr(auth, "fetch_account", lambda: account)
    return sent


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


class TestRemote:
    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/acme/widget",
            "https://github.com/acme/widget.git",
            "https://token@github.com/acme/widget.git",
            "git@github.com:acme/widget.git",
            "ssh://git@github.com/acme/widget.git",
            "https://github.com/acme/widget/",
        ],
    )
    def test_reads_github_remotes(self, url):
        assert pub.parse_github_remote(url) == ("acme", "widget")

    @pytest.mark.parametrize(
        "url",
        [
            "https://gitlab.com/acme/widget.git",
            "git@bitbucket.org:acme/widget.git",
            "/srv/widget.git",
            "",
        ],
    )
    def test_refuses_everything_else(self, url):
        assert pub.parse_github_remote(url) is None

    def test_no_remote_says_push_to_github_first(self, repo, monkeypatch):
        _git(repo, "remote", "remove", "origin")
        sent = _answer(monkeypatch, 200, {})
        result = pub.publish(repo)
        assert result.outcome == "not_github"
        assert "Push this repo to GitHub first" in result.message
        assert sent == []

    def test_non_github_remote_is_not_published(self, repo, monkeypatch):
        _git(repo, "remote", "set-url", "origin", "https://gitlab.com/acme/widget.git")
        _sign_in()
        sent = _answer(monkeypatch, 200, {})
        assert pub.publish(repo).outcome == "not_github"
        assert sent == []


class TestPublish:
    def test_signed_out_does_not_call_the_platform(self, repo, monkeypatch):
        sent = _answer(monkeypatch, 200, {})
        result = pub.publish(repo)
        assert result.outcome == "signed_out"
        assert sent == []

    def test_unpushed_branch_publishes_the_default_branch(self, repo, monkeypatch):
        _sign_in()
        sent = _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})
        pub.publish(repo)
        assert sent == [("repos/index", {"url": "https://github.com/acme/widget"})]

    def test_pushed_branch_is_the_ref(self, repo, monkeypatch):
        _git(repo, "update-ref", "refs/remotes/origin/feature", "HEAD")
        _git(repo, "config", "branch.main.remote", "origin")
        _git(repo, "config", "branch.main.merge", "refs/heads/feature")
        _sign_in()
        sent = _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})
        pub.publish(repo)
        assert sent[0][1]["ref"] == "feature"

    def test_ref_option_wins(self, repo, monkeypatch):
        _sign_in()
        sent = _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})
        pub.publish(repo, ref="release")
        assert sent[0][1]["ref"] == "release"

    def test_success_links_the_indexing_page_repo_page_and_mcp(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})
        result = pub.publish(repo)
        assert result.outcome == "published"
        assert result.url == result.open_url
        assert result.url.startswith("https://repowise.dev/s/s1/indexing?")
        assert _query(result.url) == {"src": ["cli_publish"]}
        text = "\n".join(result.details)
        assert "https://repowise.dev/repo/acme/widget?src=cli_publish" in text
        assert "https://api.repowise.dev/mcp/acme/widget" in text
        assert "10 minutes" in text

    def test_links_carry_no_install_id_with_telemetry_off(self, repo, monkeypatch):
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        _sign_in()
        _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})
        result = pub.publish(repo)
        assert _query(result.url) == {"src": ["cli_publish"]}

    def test_already_indexed_links_the_repo_page(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 200, {"short_id": "s1", "status": "ready", "cached": True})
        result = pub.publish(repo)
        assert result.outcome == "published"
        assert "already indexed" in result.message
        assert result.url.startswith("https://repowise.dev/repo/acme/widget?")
        assert result.open_url is None

    def test_dispatch_failure_is_an_error(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 200, {"short_id": "s1", "status": "failed"})
        assert pub.publish(repo).outcome == "error"


def _refusal(code: str, message: str = "nope") -> dict:
    return {"detail": {"code": code, "message": message}}


class TestRefusals:
    @pytest.mark.parametrize(
        ("status", "code"),
        [
            (400, "repo_not_found_or_private"),
            (403, "private_repo_needs_plan"),
            (403, "private_repo_needs_app"),
        ],
    )
    def test_free_private_repo_goes_to_the_trial_flow_on_its_page(
        self, repo, monkeypatch, status, code
    ):
        _sign_in()
        _answer(
            monkeypatch,
            status,
            _refusal(code),
            account={"tier": "free", "trial_eligible": True, "trial_days": 10},
        )
        result = pub.publish(repo)
        assert result.outcome == "needs_plan"
        assert "10-day free Pro trial (card required" in result.message
        assert result.url.startswith("https://repowise.dev/repo/acme/widget?")
        assert _query(result.url) == {"src": ["cli_publish"]}

    def test_private_repo_without_a_trial_offers_the_upgrade(self, repo, monkeypatch):
        _sign_in()
        _answer(
            monkeypatch,
            403,
            _refusal("private_repo_needs_plan"),
            account={"tier": "free", "trial_eligible": False, "trial_days": 10},
        )
        result = pub.publish(repo)
        assert result.outcome == "needs_plan"
        assert "trial" not in result.message
        assert "Upgrade to Pro" in result.message

    @pytest.mark.parametrize("code", ["repo_not_found_or_private", "private_repo_needs_app"])
    def test_paid_private_repo_needs_the_github_app(self, repo, monkeypatch, code):
        _sign_in()
        _answer(monkeypatch, 403, _refusal(code), account={"tier": "pro"})
        result = pub.publish(repo)
        assert result.outcome == "needs_app"
        assert result.url == pub.GITHUB_APP_INSTALL_URL

    def test_free_cap_offers_the_trial_checkout(self, repo, monkeypatch):
        _sign_in()
        _answer(
            monkeypatch,
            402,
            {"detail": "You've reached your plan's limit of 2 indexed repositories."},
            account={"tier": "free", "trial_eligible": True, "trial_days": 10},
        )
        result = pub.publish(repo)
        assert result.outcome == "cap"
        assert result.message.startswith(
            "Free accounts index 2 repos. Start a 10-day Pro trial for 5 repos and private ones"
        )
        assert "card required" in result.message
        assert urlparse(result.url).path == "/pricing"
        assert _query(result.url) == {
            "checkout": ["pro"],
            "interval": ["monthly"],
            "trial": ["1"],
            "src": ["cli_publish"],
        }

    def test_free_cap_without_a_trial_links_pricing(self, repo, monkeypatch):
        _sign_in()
        _answer(
            monkeypatch, 402, {"detail": "limit"}, account={"tier": "free", "trial_eligible": False}
        )
        result = pub.publish(repo)
        assert result.outcome == "cap"
        assert "trial" not in result.message
        assert "trial" not in _query(result.url)

    def test_pro_cap_shows_the_platform_message(self, repo, monkeypatch):
        _sign_in()
        _answer(
            monkeypatch, 402, {"detail": "Upgrade to Teams for 25 repos."}, account={"tier": "pro"}
        )
        result = pub.publish(repo)
        assert result.outcome == "cap"
        assert result.message == "Upgrade to Teams for 25 repos."

    def test_too_big_names_both_limits(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 413, {"detail": "Repo is 900 MB; Free indexes up to 250 MB."})
        result = pub.publish(repo)
        assert result.outcome == "too_big"
        assert "900 MB" in result.message
        assert "5 GB" in result.details[0]

    def test_daily_limit_says_when(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 429, {"detail": "Rate limit exceeded: 10 per 1 day"})
        result = pub.publish(repo)
        assert result.outcome == "rate_limited"
        assert "tomorrow" in result.message

    def test_jobs_in_flight_says_wait(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 429, {"detail": "You have 1 index/reindex job(s) in flight already."})
        result = pub.publish(repo)
        assert result.outcome == "rate_limited"
        assert "in flight" in result.message

    def test_offline(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 0, {})
        assert pub.publish(repo).outcome == "offline"

    def test_expired_sign_in(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 401, {"detail": "Invalid token"})
        result = pub.publish(repo)
        assert result.outcome == "signed_out"
        assert "repowise login" in result.message

    def test_curated_repo_is_already_there(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 403, _refusal("repo_curated"))
        assert pub.publish(repo).outcome == "curated"

    def test_anything_else_shows_the_platform_message(self, repo, monkeypatch):
        _sign_in()
        _answer(monkeypatch, 400, {"detail": "Branch 'x' not found"})
        result = pub.publish(repo)
        assert result.outcome == "error"
        assert "Branch 'x' not found" in result.message


class TestPostJson:
    def test_returns_status_and_body_on_a_refusal(self, monkeypatch):
        import httpx

        def fake_post(url, **kwargs):
            return httpx.Response(402, json={"detail": "limit"}, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        assert PlatformClient().post_json("repos/index", {}) == (402, {"detail": "limit"})

    def test_no_answer_is_zero(self, monkeypatch):
        import httpx

        def boom(url, **kwargs):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr(httpx, "post", boom)
        assert PlatformClient().post_json("repos/index", {}) == (0, {})


class TestLinks:
    def test_site_link_with_params_and_fragment(self):
        url = links.site_link("hosted", "cli_x", fragment="mcp", params={"a": "1"})
        assert url == "https://repowise.dev/hosted?a=1&src=cli_x#mcp"

    def test_authorize_url_takes_the_source(self):
        url = auth.build_authorize_url(
            redirect_uri="http://127.0.0.1:1/callback",
            code_challenge="c",
            state="s",
            device_name=None,
            src="cli_publish",
        )
        assert _query(url)["src"] == ["cli_publish"]
        # Sign-in is the one URL that ties this install to the new account.
        assert _query(url)["aid"] == ["abc123def456"]


class TestCommand:
    def _run(self, repo: Path, *args: str):
        from repowise.cli.main import cli

        return CliRunner().invoke(cli, ["publish", str(repo), *args])

    def test_signed_out_signs_in_with_the_publish_source_then_publishes(self, repo, monkeypatch):
        from repowise.cli.commands import login_cmd

        seen = {}

        def fake_sign_in(device, *, src):
            seen["src"] = src
            _sign_in()
            return {}

        monkeypatch.setattr(login_cmd, "browser_sign_in", fake_sign_in)
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", opened.append)
        sent = _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})

        result = self._run(repo)

        assert result.exit_code == 0, result.output
        assert seen == {"src": "cli_publish"}
        assert len(sent) == 1
        assert opened and opened[0].startswith("https://repowise.dev/s/s1/indexing?")
        assert "Your repo is being indexed" in result.output
        assert "Only what's pushed to GitHub is published" in result.output

    def test_no_open_keeps_the_browser_closed(self, repo, monkeypatch):
        _sign_in()
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", opened.append)
        _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})
        result = self._run(repo, "--no-open")
        assert result.exit_code == 0, result.output
        assert opened == []

    def test_refusal_exits_non_zero_and_records_the_outcome(self, repo, monkeypatch):
        from repowise.cli.platform import telemetry

        _sign_in()
        recorded: dict = {}
        monkeypatch.setattr(telemetry, "add_command_outcome", lambda **f: recorded.update(f))
        _answer(
            monkeypatch,
            402,
            {"detail": "limit"},
            account={"tier": "free", "trial_eligible": True, "trial_days": 10},
        )
        result = self._run(repo)
        assert result.exit_code == 1
        assert recorded["outcome"] == "cap"
        assert "Start a 10-day Pro trial" in result.output

    def test_no_remote_never_signs_in(self, repo, monkeypatch):
        from repowise.cli.commands import login_cmd

        _git(repo, "remote", "remove", "origin")
        monkeypatch.setattr(login_cmd, "browser_sign_in", lambda *a, **k: pytest.fail("signed in"))
        result = self._run(repo)
        assert result.exit_code == 1
        assert "Push this repo to GitHub first" in result.output


class TestJsonMode:
    def _run(self, repo: Path, *args: str):
        from repowise.cli.main import cli

        return CliRunner().invoke(cli, ["publish", str(repo), "--format", "json", *args])

    def test_signed_out_never_starts_a_browser_sign_in(self, repo, monkeypatch):
        import json

        from repowise.cli.commands import login_cmd

        monkeypatch.setattr(login_cmd, "browser_sign_in", lambda *a, **k: pytest.fail("signed in"))
        result = self._run(repo)
        assert result.exit_code == 1
        assert json.loads(result.stdout)["outcome"] == "signed_out"

    def test_prints_the_result_and_opens_nothing(self, repo, monkeypatch):
        import json

        _sign_in()
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", opened.append)
        _answer(monkeypatch, 200, {"short_id": "s1", "status": "queued"})
        result = self._run(repo, "--src", "local_web_publish")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["outcome"] == "published"
        assert payload["repo"] == "acme/widget"
        assert _query(payload["url"])["src"] == ["local_web_publish"]
        assert opened == []

    def test_src_is_checked(self, repo):
        result = self._run(repo, "--src", "Bad Src!")
        assert result.exit_code == 2
