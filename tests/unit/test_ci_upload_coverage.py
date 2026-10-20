"""The action's coverage upload: report resolution, identity, and never failing the job."""

from __future__ import annotations

import importlib.util
import io
import json
import re
import subprocess
import urllib.error
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ci" / "github" / "upload_coverage.py"

_spec = importlib.util.spec_from_file_location("upload_coverage", SCRIPT)
up = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(up)

SHA = "A" * 40
LCOV = "SF:src/a.py\nDA:1,1\nDA:2,0\nDA:3,1\nend_of_record\n"


def test_the_step_passes_every_variable_the_script_reads() -> None:
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    step = next(s for s in action["runs"]["steps"] if s.get("id") == "upload")
    assert step["if"] == "always() && inputs.upload == 'true'"
    pattern = r"(?:env|environ)(?:\.get\(|\[)['\"]([A-Z][A-Z_]+)"
    read = set(re.findall(pattern, SCRIPT.read_text("utf-8")))
    runner_env = {"GITHUB_OUTPUT", "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"}
    assert read - runner_env <= set(step["env"])
    assert set(step["env"]) - {"PYTHONUTF8"} <= read
    assert action["outputs"]["upload"]["value"] == "${{ steps.upload.outputs.upload }}"
    # Upload alone runs no gate; an empty checks still errors without it.
    gates = next(s for s in action["runs"]["steps"] if s.get("id") == "gates")
    assert "inputs.upload != 'true'" in gates["if"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "src/a.py"], cwd=root, check=True)
    return root


def _expected() -> dict:
    return {
        "src/a.py": {
            "covered_lines": [1, 3],
            "coverable_lines": [1, 2, 3],
            "line_coverage_pct": 66.67,
        }
    }


def test_a_named_report_resolves_to_repository_paths(repo) -> None:
    (repo / "cov").mkdir()
    (repo / "cov" / "lcov.info").write_text(LCOV, encoding="utf-8")
    files, formats = up.build_report(repo, "cov/*.info\n")
    assert files == _expected()
    assert formats == ["lcov"]


def test_a_report_prefix_is_applied(repo) -> None:
    (repo / "lcov.info").write_text(LCOV.replace("src/a.py", "a.py"), encoding="utf-8")
    files, _ = up.build_report(repo, "lcov.info=src")
    assert files == _expected()


def test_config_paths_then_discovery(repo) -> None:
    (repo / ".repowise").mkdir()
    (repo / "out").mkdir()
    (repo / "out" / "cov.info").write_text(LCOV, encoding="utf-8")
    (repo / ".repowise" / "config.yaml").write_text(
        "coverage:\n  paths: [out/cov.info]\n", encoding="utf-8"
    )
    assert up.build_report(repo, "")[0] == _expected()
    (repo / ".repowise" / "config.yaml").unlink()
    (repo / "lcov.info").write_text(LCOV, encoding="utf-8")
    assert up.build_report(repo, "")[0] == _expected()


def test_a_missing_report_stops_the_upload(repo) -> None:
    with pytest.raises(up.UploadError, match="no coverage report matches"):
        up.build_report(repo, "nope.info")
    with pytest.raises(up.UploadError, match="No coverage report found"):
        up.build_report(repo, "")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNet:
    """Stands in for ``urlopen``: the token endpoint and the upload endpoint."""

    def __init__(self, *, token: object = "jwt-secret", reply: object = None) -> None:
        self.token, self.reply, self.posts = token, reply, []

    def __call__(self, request, timeout=None):
        if request.get_method() == "GET":
            if isinstance(self.token, Exception):
                raise self.token
            assert "audience=repowise" in request.full_url
            return _Response(json.dumps({"value": self.token}).encode())
        self.posts.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return _Response(json.dumps(self.reply or {}).encode())


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "COVERAGE_REPORT": "lcov.info",
        "UPLOAD_URL": "https://api.example.test/ci/coverage",
        "HEAD_SHA": SHA,
        "PR_NUMBER": "12",
        "REPOSITORY": "owner/name",
        "EVENT_NAME": "pull_request",
        "RUN_URL": "https://github.com/owner/name/actions/runs/1",
        "FROM_FORK": "false",
        "REPO_PRIVATE": "false",
        **extra,
    }


def _run(monkeypatch, tmp_path, repo, net, **env) -> str:
    """Run the script; the ``upload`` output it set last."""
    (repo / "lcov.info").write_text(LCOV, encoding="utf-8")
    monkeypatch.setattr(up.urllib.request, "urlopen", net)
    monkeypatch.chdir(repo)
    for key in list(up.os.environ):
        if key.startswith("ACTIONS_ID_TOKEN") or key in _env(tmp_path):
            monkeypatch.delenv(key)
    for key, value in _env(tmp_path, **env).items():
        monkeypatch.setenv(key, value)
    assert up.main() == 0
    return (tmp_path / "out").read_text(encoding="utf-8").splitlines()[-1]


_OIDC = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://token.test/?api-version=2.0",
         "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "req"}


def test_an_oidc_upload_sends_the_normalized_report(monkeypatch, tmp_path, repo, capsys) -> None:
    reply = {"status": "accepted", "files": 1, "covered_lines": 2, "coverable_lines": 3,
             "bot_notified": True}
    net = FakeNet(reply=reply)
    output = _run(monkeypatch, tmp_path, repo, net, **_OIDC)
    assert output == "upload=accepted"
    (request,) = net.posts
    assert request.get_header("Authorization") == "Bearer jwt-secret"
    body = json.loads(request.data)
    assert body == {
        "repository": "owner/name",
        "head_sha": SHA.lower(),
        "pr_number": 12,
        "event": "pull_request",
        "report": {"format": "repowise-coverage-v1", "commit_sha": SHA.lower(),
                   "files": _expected()},
        "run_url": "https://github.com/owner/name/actions/runs/1",
        "repowise_version": body["repowise_version"],
        "source_formats": ["lcov"],
    }
    out = capsys.readouterr().out
    assert "1 files, 2 of 3 coverable lines" in out and "notified: yes" in out
    assert "jwt-secret" not in out


def test_a_fork_without_a_token_uploads_tokenless(monkeypatch, tmp_path, repo) -> None:
    net = FakeNet(reply={"status": "accepted"})
    output = _run(monkeypatch, tmp_path, repo, net, FROM_FORK="true")
    assert output == "upload=accepted"
    assert net.posts[0].get_header("Authorization") is None


def test_a_fork_of_a_private_repository_skips(monkeypatch, tmp_path, repo, capsys) -> None:
    net = FakeNet()
    output = _run(monkeypatch, tmp_path, repo, net, FROM_FORK="true", REPO_PRIVATE="true")
    assert output == "upload=skipped" and not net.posts
    assert "fork of a private repository" in capsys.readouterr().out


def test_a_fork_falls_back_to_tokenless_when_the_token_fails(monkeypatch, tmp_path, repo) -> None:
    net = FakeNet(token=urllib.error.URLError("down"), reply={})
    output = _run(monkeypatch, tmp_path, repo, net, FROM_FORK="true", **_OIDC)
    assert output == "upload=accepted"
    assert net.posts[0].get_header("Authorization") is None


def test_no_identity_skips_with_a_warning(monkeypatch, tmp_path, repo, capsys) -> None:
    net = FakeNet()
    output = _run(monkeypatch, tmp_path, repo, net)
    assert output == "upload=skipped" and not net.posts
    assert "id-token: write" in capsys.readouterr().out
    # A push has no pull request, so it is never tokenless.
    output = _run(monkeypatch, tmp_path, repo, net, FROM_FORK="true", PR_NUMBER="")
    assert output == "upload=skipped"


def test_a_failed_token_request_fails_without_failing_the_job(
    monkeypatch, tmp_path, repo, capsys
) -> None:
    net = FakeNet(token=urllib.error.URLError("down"))
    output = _run(monkeypatch, tmp_path, repo, net, **_OIDC)
    assert output == "upload=failed" and not net.posts
    assert "Could not get a GitHub OIDC token" in capsys.readouterr().out


def test_the_servers_reason_is_printed(monkeypatch, tmp_path, repo, capsys) -> None:
    payload = {"detail": "The Repowise PR bot is not installed on owner/name.\n::error::x",
               "hint": "https://repowise.dev/bot"}
    refused = urllib.error.HTTPError(
        "u", 404, "Not Found", {}, io.BytesIO(json.dumps(payload).encode())
    )
    output = _run(monkeypatch, tmp_path, repo, FakeNet(reply=refused), **_OIDC)
    assert output == "upload=failed"
    (line,) = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning")]
    assert "Upload refused (404)" in line and "Hint: https://repowise.dev/bot" in line
    # The server's newline cannot start a workflow command of its own.
    assert "%0A::error::x" in line


def test_an_unexpected_error_never_fails_the_job(monkeypatch, tmp_path, repo) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(up, "build_report", boom)
    output = _run(monkeypatch, tmp_path, repo, FakeNet(), **_OIDC)
    assert output == "upload=failed"
