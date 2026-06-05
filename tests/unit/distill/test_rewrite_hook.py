"""repowise-rewrite PreToolUse hook — classification, bailouts, permissions.

The decision table here is the contract: payload in → rewrite/passthrough
out. The hook is allowlist-based (only table families are rewritten) and
every ambiguity resolves to passthrough.
"""

from __future__ import annotations

import io
import json
import sys

import pytest
import yaml

from repowise.cli import rewrite_hook
from repowise.cli.rewrite_hook import FAMILY_PATTERNS, _normalize, classify, decide

# ---------------------------------------------------------------------------
# Classification decision table
# ---------------------------------------------------------------------------


class TestClassify:
    @pytest.mark.parametrize(
        ("command", "family"),
        [
            # test runners
            ("pytest -x", "test_output"),
            ("pytest tests/unit -q", "test_output"),
            ("uv run pytest tests/unit", "test_output"),
            ("python -m pytest tests", "test_output"),
            (r".venv\Scripts\pytest.exe -q", "test_output"),
            ("npm test", "test_output"),
            ("yarn test", "test_output"),
            ("cargo test", "test_output"),
            ("go test ./...", "test_output"),
            ("npx vitest run", "test_output"),
            # builds
            ("npm run build", "build_output"),
            ("npm run type-check", "build_output"),
            ("tsc --noEmit", "build_output"),
            ("cargo build --release", "build_output"),
            ("go vet ./...", "build_output"),
            ("make", "build_output"),
            # git
            ("git status", "git_status"),
            ("git log --oneline -20", "git_log"),
            ("git diff main", "git_diff"),
            ("git show HEAD~2", "git_diff"),
            # search / listings / logs
            ("rg TODO src/", "search_results"),
            ("grep -rn auth .", "search_results"),
            ("git grep parse_yaml", "search_results"),
            ("ls -la", "file_listing"),
            ("find . -name '*.py'", "file_listing"),
            ("tree src", "file_listing"),
            ("tail -n 100 app.log", "logs"),
            ("cat server.log", "logs"),
        ],
    )
    def test_rewrites(self, command: str, family: str) -> None:
        assert classify(command) == family

    @pytest.mark.parametrize(
        "command",
        [
            # ignore-list
            "cd packages/core",
            "echo hello",
            "mkdir -p foo",
            "rm -rf build",
            "mv a b",
            "cp a b",
            "touch x",
            "pwd",
            "which python",
            "sleep 5",
            # interactive
            "vim foo.py",
            "less README.md",
            "ssh host",
            "python script.py",
            "node server.js",
            # compound / pipes / redirections / substitution
            "pytest | head -50",
            "pytest && echo done",
            "pytest || true",
            "git status; ls",
            "pytest > out.txt 2>&1",
            "pytest < input.txt",
            "git log `git rev-parse HEAD`",
            "pytest $(cat args.txt)",
            "pytest -x &",
            "pytest -x\ngit status",
            "find . -name '*.tmp' -exec rm {} ;",
            # watch / follow modes
            "vitest --watch",
            "npm test -- --watchAll",
            "pytest --looponfail",
            "tail -f app.log",
            "kubectl logs --follow pod",
            # already repowise
            "repowise distill pytest -x",
            "repowise update",
            "repowise-augment",
            # opted-out variants
            "git status --porcelain",
            "git diff --stat",
            # non-table commands
            "docker compose up",
            "curl https://example.com",
            "",
            "   ",
        ],
    )
    def test_passthrough(self, command: str) -> None:
        assert classify(command) is None

    def test_family_names_match_registered_filters(self) -> None:
        """Per-family config keys must be real filter names in the core registry."""
        from repowise.core.distill import filter_registry

        registered = {f.name for f in filter_registry.filters()}
        for family, _ in FAMILY_PATTERNS:
            assert family in registered

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -x",
            "FOO=bar pytest",
            "uv run python -m pytest tests",
            r".venv\Scripts\pytest.exe -q",
            "npx jest --ci",
            "git status",
            "LS_COLORS=1 ls -la",
        ],
    )
    def test_normalize_mirrors_core_router(self, command: str) -> None:
        """The hot-path normalizer must stay behaviorally identical to core's."""
        from repowise.core.distill.router import normalize_command

        assert _normalize(command) == normalize_command(command)


# ---------------------------------------------------------------------------
# decide() — repo gating + permission config
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".repowise").mkdir()
    return tmp_path


def _write_config(repo, distill_block) -> None:
    (repo / ".repowise" / "config.yaml").write_text(
        yaml.dump({"distill": distill_block}), encoding="utf-8"
    )


class TestDecide:
    def test_default_is_ask(self, repo) -> None:
        result = decide("pytest -x", str(repo))
        assert result is not None
        assert result.command == "repowise distill pytest -x"
        assert result.permission == "ask"
        assert "repowise expand" in result.reason

    def test_no_repowise_dir_passes_through(self, tmp_path) -> None:
        assert decide("pytest -x", str(tmp_path)) is None

    def test_cwd_below_repo_root_is_found(self, repo) -> None:
        nested = repo / "packages" / "core"
        nested.mkdir(parents=True)
        assert decide("pytest -x", str(nested)) is not None

    def test_distill_disabled(self, repo) -> None:
        _write_config(repo, {"enabled": False})
        assert decide("pytest -x", str(repo)) is None

    def test_commands_disabled(self, repo) -> None:
        _write_config(repo, {"commands": {"enabled": False}})
        assert decide("pytest -x", str(repo)) is None

    def test_permission_off_disables(self, repo) -> None:
        _write_config(repo, {"commands": {"permission": "off"}})
        assert decide("pytest -x", str(repo)) is None

    def test_global_allow(self, repo) -> None:
        _write_config(repo, {"commands": {"permission": "allow"}})
        assert decide("git status", str(repo)).permission == "allow"

    def test_family_allow_overrides_ask(self, repo) -> None:
        _write_config(repo, {"commands": {"families": {"git_status": "allow"}}})
        assert decide("git status", str(repo)).permission == "allow"
        assert decide("pytest -x", str(repo)).permission == "ask"

    def test_family_off_disables_one_family(self, repo) -> None:
        _write_config(repo, {"commands": {"families": {"git_diff": "off"}}})
        assert decide("git diff main", str(repo)) is None
        assert decide("git status", str(repo)) is not None

    def test_family_deny_alias(self, repo) -> None:
        _write_config(repo, {"commands": {"families": {"git_diff": "deny"}}})
        assert decide("git diff main", str(repo)) is None

    def test_malformed_config_defaults_to_ask(self, repo) -> None:
        (repo / ".repowise" / "config.yaml").write_text(
            "distill: [not, a, mapping", encoding="utf-8"
        )
        result = decide("pytest -x", str(repo))
        assert result is not None and result.permission == "ask"


# ---------------------------------------------------------------------------
# End-to-end: PreToolUse payload in, hookSpecificOutput JSON out
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, payload) -> str:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    with pytest.raises(SystemExit) as exc:
        rewrite_hook.main()
    assert exc.value.code == 0
    return stdout.getvalue()


def _payload(command: str, cwd: str, **overrides):
    base = {
        "session_id": "abc123",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    base.update(overrides)
    return base


class TestMain:
    def test_rewrite_response_shape(self, monkeypatch, repo) -> None:
        out = _run_main(monkeypatch, _payload("pytest -x", str(repo)))
        response = json.loads(out)
        hso = response["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["permissionDecision"] == "ask"
        assert hso["updatedInput"] == {"command": "repowise distill pytest -x"}
        assert hso["permissionDecisionReason"]

    def test_passthrough_emits_nothing(self, monkeypatch, repo) -> None:
        assert _run_main(monkeypatch, _payload("cd src", str(repo))) == ""

    def test_non_bash_tool_ignored(self, monkeypatch, repo) -> None:
        assert _run_main(monkeypatch, _payload("pytest", str(repo), tool_name="Grep")) == ""

    def test_post_tool_use_ignored(self, monkeypatch, repo) -> None:
        payload = _payload("pytest", str(repo), hook_event_name="PostToolUse")
        assert _run_main(monkeypatch, payload) == ""

    def test_malformed_json_is_silent(self, monkeypatch) -> None:
        assert _run_main(monkeypatch, "{not json") == ""

    def test_empty_stdin_is_silent(self, monkeypatch) -> None:
        assert _run_main(monkeypatch, "") == ""
