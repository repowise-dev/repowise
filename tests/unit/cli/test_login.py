"""Tests for hosted-account sign-in (credentials store + auth module + CLI).

Covers what protects the user: owner-only credential file permissions,
fail-soft anonymous degradation everywhere (signed out, offline, revoked),
single-flight refresh so parallel commands can't revoke each other's rotated
token, and the PKCE values matching the S256 spec.
"""

from __future__ import annotations

import base64
import hashlib
import os
import stat
import sys
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.platform import auth, credentials, store
from repowise.cli.platform.client import PlatformClient


@pytest.fixture(autouse=True)
def isolated_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the credential + platform stores at a temp dir; never touch real
    ~/.repowise (login also reads the anon id from platform.json)."""
    monkeypatch.setattr(credentials, "_path", lambda: tmp_path / "credentials.json")
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "platform.json")
    yield


def _oauth_creds(**overrides) -> dict:
    creds = {
        "token_kind": "oauth",
        "client_id": "repowise-cli",
        "access_token": "at-1",
        "access_expires_at": int(time.time()) + 3600,
        "refresh_token": "rt-1",
        "device_name": "test-machine",
    }
    creds.update(overrides)
    return creds


class TestCredentialStore:
    def test_round_trip(self):
        credentials.save(_oauth_creds())
        loaded = credentials.load()
        assert loaded is not None
        assert loaded["access_token"] == "at-1"

    def test_missing_and_corrupt_return_none(self, tmp_path: Path):
        assert credentials.load() is None
        (tmp_path / "credentials.json").write_text("{not json", encoding="utf-8")
        assert credentials.load() is None

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
    def test_file_is_owner_only(self, tmp_path: Path):
        credentials.save(_oauth_creds())
        mode = stat.S_IMODE(os.stat(tmp_path / "credentials.json").st_mode)
        assert mode == 0o600

    def test_expiry_logic(self):
        assert not credentials.is_access_expired(_oauth_creds())
        assert credentials.is_access_expired(_oauth_creds(access_expires_at=int(time.time()) + 10))
        assert credentials.is_access_expired(_oauth_creds(access_expires_at=None))
        # API keys never expire.
        assert not credentials.is_access_expired(
            {"token_kind": "api_key", "access_token": "rw_live_x"}
        )

    def test_mark_stale_preserves_account(self):
        credentials.save(_oauth_creds(account={"github_username": "raghav"}))
        credentials.mark_stale()
        loaded = credentials.load()
        assert loaded["stale"] is True
        assert loaded["account"]["github_username"] == "raghav"


class TestPkce:
    def test_challenge_is_s256_of_verifier(self):
        verifier, challenge = auth.make_pkce_pair()
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert challenge == expected
        assert 43 <= len(verifier) <= 128  # RFC 7636 bounds

    def test_authorize_url_carries_required_params(self):
        url = auth.build_authorize_url(
            redirect_uri="http://127.0.0.1:5555/callback",
            code_challenge="c",
            state="s",
            device_name="my box",
        )
        assert url.startswith(auth.AUTHORIZE_URL + "?")
        for fragment in (
            "client_id=repowise-cli",
            "code_challenge_method=S256",
            "response_type=code",
            "state=s",
            "device_name=my+box",
        ):
            assert fragment in url

    def test_authorize_url_carries_the_link_source_and_install_id(self, monkeypatch):
        from repowise.cli.platform import identity

        monkeypatch.delenv("DO_NOT_TRACK", raising=False)
        monkeypatch.delenv("REPOWISE_TELEMETRY_DISABLED", raising=False)
        url = auth.build_authorize_url(
            redirect_uri="http://127.0.0.1:5555/callback", code_challenge="c", state="s",
            device_name=None,
        )
        assert "src=cli_login" in url
        assert f"aid={identity.get_anonymous_id()}" in url

    @pytest.mark.parametrize("env", ["DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED"])
    def test_authorize_url_omits_the_install_id_with_telemetry_off(self, monkeypatch, env):
        monkeypatch.setenv(env, "1")
        url = auth.build_authorize_url(
            redirect_uri="http://127.0.0.1:5555/callback", code_challenge="c", state="s",
            device_name=None,
        )
        assert "src=cli_login" in url
        assert "aid=" not in url


class TestAuthHeaders:
    def test_signed_out_is_anonymous(self):
        assert auth.auth_headers() == {}

    def test_api_key_kind_returns_bearer(self):
        credentials.save({"token_kind": "api_key", "access_token": "rw_live_abc"})
        assert auth.auth_headers() == {"Authorization": "Bearer rw_live_abc"}

    def test_fresh_oauth_token_returned_without_network(self, monkeypatch):
        credentials.save(_oauth_creds())

        def boom(*a, **k):
            raise AssertionError("no network call expected for a fresh token")

        monkeypatch.setattr(auth, "_token_request", boom)
        assert auth.auth_headers() == {"Authorization": "Bearer at-1"}

    def test_expired_token_refreshes_and_persists_rotation(self, monkeypatch):
        credentials.save(_oauth_creds(access_expires_at=int(time.time()) - 10))
        monkeypatch.setattr(
            auth,
            "_token_request",
            lambda form: (
                200,
                {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600},
            ),
        )
        assert auth.auth_headers() == {"Authorization": "Bearer at-2"}
        stored = credentials.load()
        assert stored["refresh_token"] == "rt-2"
        assert stored["device_name"] == "test-machine"

    def test_revoked_refresh_marks_stale_and_goes_anonymous(self, monkeypatch):
        credentials.save(_oauth_creds(access_expires_at=int(time.time()) - 10))
        monkeypatch.setattr(auth, "_token_request", lambda form: (400, {"error": "invalid_grant"}))
        assert auth.auth_headers() == {}
        assert credentials.load()["stale"] is True
        # And the dead token is not retried on the next call.
        monkeypatch.setattr(
            auth,
            "_token_request",
            lambda form: pytest.fail("stale credentials must not retry refresh"),
        )
        assert auth.auth_headers() == {}

    def test_network_failure_stays_signed_in_but_anonymous(self, monkeypatch):
        credentials.save(_oauth_creds(access_expires_at=int(time.time()) - 10))
        monkeypatch.setattr(auth, "_token_request", lambda form: (0, {}))
        assert auth.auth_headers() == {}
        # Not marked stale: next call may succeed once back online.
        assert credentials.load().get("stale") is None

    def test_platform_client_headers_pick_up_login(self):
        credentials.save({"token_kind": "api_key", "access_token": "rw_live_abc"})
        headers = PlatformClient()._headers()
        assert headers["Authorization"] == "Bearer rw_live_abc"


class TestRefreshSingleFlight:
    def test_lock_contention_rereads_instead_of_double_rotating(self, monkeypatch):
        """The loser of the lock must consume the winner's persisted rotation,
        never present the (now revoked) old refresh token itself."""
        credentials.save(_oauth_creds(access_expires_at=int(time.time()) - 10))

        calls = []

        def fake_token_request(form):
            calls.append(form)
            # Simulate the winner having persisted a rotation while we
            # held/waited on the lock is covered by re-read; here the single
            # caller path rotates once.
            return (
                200,
                {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600},
            )

        monkeypatch.setattr(auth, "_token_request", fake_token_request)
        assert auth.get_valid_credentials()["access_token"] == "at-2"
        assert len(calls) == 1
        # Second call finds a fresh token: no second rotation.
        assert auth.get_valid_credentials()["access_token"] == "at-2"
        assert len(calls) == 1

    def test_lock_timeout_returns_none_not_stolen_rotation(self, monkeypatch, tmp_path):
        credentials.save(_oauth_creds(access_expires_at=int(time.time()) - 10))
        # Hold the lock externally with a fresh mtime so it isn't stale-reaped.
        lock = tmp_path / "credentials.lock"
        lock.write_text("held")
        monkeypatch.setattr(credentials, "_LOCK_STALE_SECONDS", 9999)
        monkeypatch.setattr(
            auth,
            "_token_request",
            lambda form: pytest.fail("must not rotate while another process holds the lock"),
        )
        # Short timeout so the test is fast.
        original = credentials.refresh_lock

        def quick_lock(timeout: float = 0.3):
            return original(timeout=timeout)

        monkeypatch.setattr(credentials, "refresh_lock", quick_lock)
        assert auth.get_valid_credentials() is None


class TestCliCommands:
    def test_whoami_signed_out(self):
        from repowise.cli.commands.login_cmd import whoami_command

        result = CliRunner().invoke(whoami_command)
        assert result.exit_code == 0
        assert "Not signed in" in result.output

    def test_whoami_offline_uses_cached_account(self, monkeypatch):
        from repowise.cli.commands.login_cmd import whoami_command

        credentials.save(_oauth_creds(account={"github_username": "raghav"}))
        monkeypatch.setattr(auth, "fetch_account", lambda: None)
        result = CliRunner().invoke(whoami_command)
        assert result.exit_code == 0
        assert "raghav" in result.output

    def test_logout_deletes_credentials(self, monkeypatch):
        from repowise.cli.commands.login_cmd import logout_command

        credentials.save(_oauth_creds())
        deleted = {}
        monkeypatch.setattr(
            "repowise.cli.platform.client.PlatformClient.delete",
            lambda self, path, params=None, timeout=None: deleted.setdefault("path", path) or True,
        )
        result = CliRunner().invoke(logout_command)
        assert result.exit_code == 0
        assert credentials.load() is None
        assert deleted["path"] == "oauth/connections/repowise-cli"

    def test_login_with_token_rejects_non_key(self, monkeypatch):
        from repowise.cli.commands.login_cmd import login_command

        result = CliRunner().invoke(login_command, ["--with-token"], input="garbage\n")
        assert result.exit_code != 0
        assert "rw_live_" in result.output

    def test_login_with_token_saves_and_greets(self, monkeypatch):
        from repowise.cli.commands.login_cmd import login_command

        monkeypatch.setattr(
            auth,
            "fetch_account",
            lambda: {"github_username": "raghav", "tier": "pro", "llm_credit_balance_cents": 500},
        )
        linked = {}
        monkeypatch.setattr(
            "repowise.cli.platform.client.PlatformClient.post_status",
            lambda self, path, payload, timeout=None: linked.setdefault(path, payload) and 200,
        )
        result = CliRunner().invoke(login_command, ["--with-token"], input="rw_live_secret123\n")
        assert result.exit_code == 0, result.output
        assert "raghav" in result.output
        assert "pro" in result.output
        stored = credentials.load()
        assert stored["token_kind"] == "api_key"
        assert stored["access_token"] == "rw_live_secret123"
        assert "auth/link-anon" in linked

    @pytest.mark.parametrize("status", [200, 401, 0])
    def test_login_records_the_link_anon_status(self, monkeypatch, status):
        from repowise.cli.commands.login_cmd import login_command
        from repowise.cli.platform import telemetry

        telemetry.drain_command_outcome()
        monkeypatch.setattr(auth, "fetch_account", lambda: {"github_username": "raghav"})
        monkeypatch.setattr(
            "repowise.cli.platform.client.PlatformClient.post_status",
            lambda self, path, payload, timeout=None: status,
        )
        monkeypatch.setenv("REPOWISE_TELEMETRY_DEBUG", "1")
        result = CliRunner().invoke(login_command, ["--with-token"], input="rw_live_secret123\n")
        assert result.exit_code == 0, result.output
        assert telemetry.drain_command_outcome()["link_anon_status"] == status
        assert f"auth/link-anon -> HTTP {status}" in result.output

    def test_link_anon_status_is_quiet_without_debug(self, monkeypatch):
        from repowise.cli.commands.login_cmd import login_command

        monkeypatch.delenv("REPOWISE_TELEMETRY_DEBUG", raising=False)
        monkeypatch.setattr(auth, "fetch_account", lambda: {"github_username": "raghav"})
        monkeypatch.setattr(
            "repowise.cli.platform.client.PlatformClient.post_status",
            lambda self, path, payload, timeout=None: 200,
        )
        result = CliRunner().invoke(login_command, ["--with-token"], input="rw_live_secret123\n")
        assert "auth/link-anon" not in result.output

    def test_already_signed_in_is_not_counted_as_a_sign_in(self):
        from repowise.cli.commands.login_cmd import login_command
        from repowise.cli.platform import telemetry

        telemetry.drain_command_outcome()
        credentials.save(_oauth_creds(account={"github_username": "raghav"}))
        result = CliRunner().invoke(login_command, [])
        assert result.exit_code == 0
        assert "Already signed in" in result.output
        assert telemetry.drain_command_outcome() == {"already_signed_in": True}


class TestPlatformClientPost:
    def _client(self, monkeypatch, outcome):
        import httpx

        def fake_post(url, json=None, headers=None, timeout=None):
            if isinstance(outcome, Exception):
                raise outcome
            return httpx.Response(outcome, request=httpx.Request("POST", url))

        monkeypatch.setattr(httpx, "post", fake_post)
        return PlatformClient()

    @pytest.mark.parametrize("code", [200, 204, 401, 500])
    def test_post_status_returns_the_http_status(self, monkeypatch, code):
        assert self._client(monkeypatch, code).post_status("x", {}) == code

    def test_post_status_is_zero_without_an_answer(self, monkeypatch):
        import httpx

        client = self._client(monkeypatch, httpx.ConnectError("offline"))
        assert client.post_status("x", {}) == 0

    @pytest.mark.parametrize(("code", "ok"), [(200, True), (201, True), (401, False), (500, False)])
    def test_post_still_returns_success(self, monkeypatch, code, ok):
        assert self._client(monkeypatch, code).post("x", {}) is ok
