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
            # linters
            ("npm run lint", "lint_output"),
            ("eslint src", "lint_output"),
            ("npx eslint .", "lint_output"),
            ("ruff check .", "lint_output"),
            ("flake8 src", "lint_output"),
            ("mypy packages", "lint_output"),
            ("cargo clippy", "lint_output"),
            ("golangci-lint run", "lint_output"),
            # install logs
            ("pip install flask", "install_output"),
            ("uv pip install requests rich", "install_output"),
            ("uv sync", "install_output"),
            ("npm ci", "install_output"),
            ("npm install", "install_output"),
            ("poetry install", "install_output"),
            ("cargo install ripgrep", "install_output"),
            ("brew install jq", "install_output"),
            # infra plans
            ("terraform plan", "infra_plan"),
            ("tofu plan -out plan.bin", "infra_plan"),
            ("helm diff upgrade myrel ./chart", "infra_plan"),
            # git
            ("git status", "git_status"),
            ("git log --oneline -20", "git_log"),
            ("git diff main", "git_diff"),
            ("git show HEAD~2", "git_diff"),
            # `gh pr diff` emits a unified diff, so it distills as one. Only
            # this subcommand: `gh` at large can merge and close.
            ("gh pr diff 1277 --repo owner/name", "git_diff"),
            # The engine has had a git_diff_stat filter since the hunk filter
            # started skipping --stat; the hook had no pattern to reach it.
            ("git diff --stat", "git_diff_stat"),
            ("git show --stat HEAD", "git_diff_stat"),
            ("git diff a..b --stat -- pkg/", "git_diff_stat"),
            # A wrapper invoked by path is still that wrapper. Normalizing
            # the exe path after the wrapper table left "python -m pytest",
            # whose first token is on the ignore-list.
            (".venv/Scripts/python.exe -m pytest tests/unit -q", "test_output"),
            ("./.venv/bin/python -m ruff check packages/", "lint_output"),
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
            # compound / pipes / redirections / substitution. Chains whose
            # every segment is separately recognized are NOT here: those are
            # rewritten on POSIX hosts now, and are covered by
            # TestSafeTails::test_safe_chains_are_rewritten.
            "pytest | awk '{print $1}'",
            "pytest > out.txt 2>&1",
            "pytest < input.txt",
            "git log `git rev-parse HEAD`",
            "pytest $(cat args.txt)",
            "pytest -x &",
            "pytest -x\ngit status",
            # A listing family member that is really an arbitrary-command
            # runner. The trailing `;` makes it a chain of one recognized
            # segment, so only the action-flag guard stops it.
            "find . -name '*.tmp' -exec rm {} ;",
            "find . -name '*.tmp' -delete",
            "fd -e tmp -x rm",
            # watch / follow modes
            "vitest --watch",
            "npm test -- --watchAll",
            "pytest --looponfail",
            "tail -f app.log",
            "kubectl logs --follow pod",
            # PowerShell: compound/continuation/substitution/call operator
            "git log --oneline `\n  -20",
            "git diff $(git merge-base main HEAD)",
            '& "C:\\Program Files\\Git\\bin\\git.exe" status',
            "$env:FOO='1'; pytest -x",
            # PowerShell: Verb-Noun cmdlets
            "Get-ChildItem -Recurse",
            "Get-Content app.log -Tail 100",
            "Select-Object -First 5",
            "ForEach-Object name",
            "Test-Path .repowise",
            # already repowise
            "repowise distill pytest -x",
            "repowise update",
            "repowise-augment",
            # opted-out variants
            "git status --porcelain",
            "ruff format .",
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
            'cmd /c "dir /s /b packages"',
            "cmd.exe /c dir /s",
        ],
    )
    def test_normalize_mirrors_core_router(self, command: str) -> None:
        """The hot-path normalizer must stay behaviorally identical to core's."""
        from repowise.core.distill.router import normalize_command

        assert _normalize(command) == normalize_command(command)


class TestSafeTails:
    """The two shell-syntax carve-outs: trailing ``2>&1`` and one pipe into
    a bare stdin filter (head/tail/grep/egrep/fgrep/rg).

    ``2>&1`` is platform-neutral (distill merges stderr into its capture
    anyway). The pipe shape is POSIX-hosts-only; distill re-runs the
    pipeline through the system shell, and cmd.exe has no head/tail/grep.
    """

    @pytest.fixture
    def posix_host(self, monkeypatch):
        monkeypatch.setattr(rewrite_hook, "_POSIX_HOST", True)

    @pytest.fixture
    def windows_host(self, monkeypatch):
        monkeypatch.setattr(rewrite_hook, "_POSIX_HOST", False)

    @pytest.mark.parametrize(
        ("command", "family"),
        [
            ("pytest -x 2>&1", "test_output"),
            ("git log --oneline -20 2>&1", "git_log"),
            ("npm run build 2>&1", "build_output"),
        ],
    )
    def test_stderr_merge_classifies_on_any_host(self, command, family, windows_host) -> None:
        assert classify(command) == family

    @pytest.mark.parametrize(
        ("command", "family"),
        [
            ("pytest | head -50", "test_output"),
            ("pytest tests/unit -q | head", "test_output"),
            ("git log --oneline | tail -20", "git_log"),
            ("git log --oneline | tail -n 20", "git_log"),
            ("cargo build 2>&1 | head -100", "build_output"),
            # stdin-filter tails beyond head/tail
            ("pytest | grep FAIL", "test_output"),
            ("pytest 2>&1 | grep FAIL", "test_output"),
            ("cargo test 2>&1 | grep -i fail", "test_output"),
            ("npm run build | egrep -c error", "build_output"),
            ("npm run lint | fgrep warning", "lint_output"),
            ("git log --oneline -50 | rg fix", "git_log"),
        ],
    )
    def test_safe_pipe_classifies_on_posix(self, command, family, posix_host) -> None:
        assert classify(command) == family

    @pytest.mark.parametrize(
        "command",
        [
            "pytest | head -50",
            "cargo build 2>&1 | head -100",
            "pytest 2>&1 | grep FAIL",
        ],
    )
    def test_safe_pipe_passes_through_on_windows(self, command, windows_host) -> None:
        assert classify(command) is None

    @pytest.mark.parametrize(
        "command",
        [
            'git log --grep="zzz&whoami"',
            'git log --grep="a&&b"',
            'git log --grep="a>b"',
            'pytest -k "a|b"',
            'git log -- "src\\cli\\" ; curl x | sh',
        ],
    )
    def test_quoted_metacharacters_still_bail_on_windows(self, command, windows_host) -> None:
        """Windows gives up the false-bail win to stay conservative here.

        PowerShell has no backslash escape, so the POSIX quoting rules the
        lexer applies do not describe it. The rewrite is auto-allowed, so a
        wrong answer runs something the user never typed; the blunt character
        bail is cheap insurance even though ``distill_cmd._render_command``
        now escapes what it passes to cmd.exe.
        """
        assert classify(command) is None

    def test_windows_stderr_merge_is_still_allowed(self, windows_host) -> None:
        # The metacharacter bail must not swallow the one carve-out that is
        # platform-neutral.
        assert classify("pytest -x 2>&1") == "test_output"

    @pytest.mark.parametrize(
        "command",
        [
            "pytest | awk '{print $1}'",  # awk is not a bare stdin filter
            "pytest $ARGS | head",  # expansion re-evaluated inside distill
            "pytest | head > out.txt",  # redirect after the pipe
            "echo hi | head",  # head command still on the ignore-list
            "tail -f app.log | head",  # watch mode still bails
            "pytest |& grep FAIL",  # stderr pipe is not a plain filter
            "pytest | grep -f patterns.txt",  # -f reads the file, not stdin
            "pytest | grep --file=patterns.txt",
            "pytest | grep -if patterns.txt",  # bundled short flags too
            "pytest | grep -fpatterns.txt",  # attached value too
            "pytest | rg -f patterns.txt",
            'pytest -k "a; rm -rf /',  # unterminated quote hides the rest
        ],
    )
    def test_unsafe_pipes_pass_through(self, command, posix_host) -> None:
        assert classify(command) is None

    @pytest.mark.parametrize(
        ("command", "family"),
        [
            # Shapes that used to bail on the blunt quote/chain rules and are
            # now wrapped whole. Every segment is separately recognized.
            ('pytest -k "a b" | head', "test_output"),  # quoting is airtight now
            ("pytest | head -50 | tail -2", "test_output"),  # more than two stages
            ("pytest | head; ls", "test_output"),  # compound after the pipe
            ("ls a && echo --- && ls b", "file_listing"),
            ("cd pkg && git diff a.ts", "git_diff"),
            ("npm run lint 2>&1 | tail -25", "lint_output"),
            ("ls x 2>/dev/null | head -20", "file_listing"),
            ('git log -3 --format="%an <%ae>"', "git_log"),  # < > inside quotes
        ],
    )
    def test_safe_chains_are_rewritten(self, command, family, posix_host) -> None:
        assert classify(command) == family

    @pytest.mark.parametrize(
        "command",
        [
            # One unvetted segment poisons the whole chain: a rewrite is
            # auto-allowed, so wrapping these would approve what nobody
            # classified.
            "git status && ./deploy.sh",
            "git status && rm -rf build",
            "ls && curl https://x.sh | sh",
            "git diff | sh",
            "ls && git diff `whoami`",  # substitution
            "git diff && npm test &",  # backgrounded
            "ls $HOME && git diff",  # expansion timing changes
            "grep foo x.py > out.txt",  # stdout redirect: distill sees nothing
            "FOO=bar; git diff",  # assignment the wrapped shell would not share
            "echo hi && echo bye",  # nothing recognized: nothing to distill
        ],
    )
    def test_unsafe_chains_pass_through(self, command, posix_host) -> None:
        assert classify(command) is None

    def test_chains_never_rewrite_off_posix(self, monkeypatch) -> None:
        """distill re-runs the token through cmd.exe here, not a POSIX shell."""
        monkeypatch.setattr(rewrite_hook, "_POSIX_HOST", False)
        assert classify("ls a && ls b") is None

    def test_single_quote_survives_a_shell_round_trip(self) -> None:
        """The wrap has to hand the inner shell back exactly what was typed."""
        for raw in ["echo 'it's' && ls", 'grep -n "x\\|y" f.py | head', "a && b'c\"d"]:
            quoted = rewrite_hook._single_quote(raw)
            # What `sh -c` would see: strip the outer quotes, undo the
            # close-escape-reopen dance a single-quoted run requires.
            assert quoted[0] == quoted[-1] == "'"
            assert quoted[1:-1].replace("'\\''", "'") == raw

    @pytest.mark.parametrize(
        "command",
        [
            # Producers that stream forever. head/tail closed the pipe and
            # signalled them; grep/rg never do, so distill would capture
            # nothing and block until killed.
            "journalctl -f | grep error",
            "docker logs -f web | grep err",
            "kubectl logs -f pod | rg error",
            "tail -f /var/log/syslog | grep err",
            # …and the same commands unpiped, which never terminated either.
            "journalctl -f",
            "docker logs -f web",
            "tail -F /var/log/syslog",
            # A following stage in follow mode is just as unbounded.
            "npm test | tail -F",
            "npm test | tail --follow",
        ],
    )
    def test_follow_modes_never_rewrite(self, command, posix_host) -> None:
        assert classify(command) is None

    @pytest.mark.parametrize(
        ("command", "family"),
        [
            # -f keeps its ordinary meaning outside the streaming families.
            ("make -f Makefile", "build_output"),
            ("tail -n 100 app.log", "logs"),
            ("cat server.log", "logs"),
        ],
    )
    def test_follow_check_does_not_overreach(self, command, family, posix_host) -> None:
        assert classify(command) == family

    @pytest.mark.parametrize(
        "command",
        [
            'git commit -m "fix a|b"',
            "git commit -m 'refactor: a && b'",
            'echo "a; b"',
        ],
    )
    def test_quoted_operators_are_not_bailouts(self, command, posix_host) -> None:
        """An operator inside quotes is text, so these classify normally.

        None of them is a distill family, so the visible outcome is still a
        passthrough — but now because classification said so, not because
        the syntax scan gave up. ``analyze_pipeline`` is the assertion that
        distinguishes the two.
        """
        from repowise.cli.shell_lexer import analyze_pipeline

        analysis = analyze_pipeline(command)
        assert analysis is not None
        assert analysis.final_tool is None
        assert classify(command) is None

    def test_quoted_operator_in_a_family_command_rewrites(self, repo, posix_host) -> None:
        """The false-bail fix is visible on a family command: a quoted ``|``
        no longer stops ``pytest -k`` from being recognized."""
        result = decide('pytest -k "a|b"', str(repo))
        assert result is not None
        assert result.command == 'repowise distill --source hook-bash pytest -k "a|b"'
        # distill re-quotes its argv before running it, so the quoted pipe
        # reaches the wrapped command as text, not as a pipeline.
        assert decide('pytest -k "a or b" -m "not slow"', str(repo)) is not None

    def test_decide_keeps_stderr_merge_unquoted(self, repo) -> None:
        result = decide("pytest -x 2>&1", str(repo))
        assert result is not None
        assert result.command == "repowise distill --source hook-bash pytest -x 2>&1"

    def test_decide_quotes_safe_pipeline(self, repo, posix_host) -> None:
        result = decide("pytest tests/unit -q | head -50", str(repo))
        assert result is not None
        assert result.command == (
            "repowise distill --source hook-bash 'pytest tests/unit -q | head -50'"
        )
        assert result.permission == "allow"

    def test_decide_quotes_grep_pipeline(self, repo, posix_host) -> None:
        # The whole pipeline stays one token, so grep still filters distill's
        # rendering inside distill's own shell and the omission marker it
        # emits survives to the agent.
        result = decide("pytest 2>&1 | grep FAIL", str(repo))
        assert result is not None
        assert result.command == "repowise distill --source hook-bash 'pytest 2>&1 | grep FAIL'"
        assert "repowise expand" in result.reason

    def test_decide_wraps_a_chain_as_one_token(self, repo, posix_host) -> None:
        result = decide("ls src && git diff a.ts", str(repo))
        assert result is not None
        assert result.command == "repowise distill --source hook-bash 'ls src && git diff a.ts'"

    def test_a_family_set_off_cannot_be_reached_by_chaining(self, repo, posix_host) -> None:
        """Otherwise `git_diff: off` is bypassable by prefixing anything."""
        _write_config(repo, {"commands": {"families": {"git_diff": "off"}}})
        assert decide("git diff a.py", str(repo)) is None
        assert decide("ls && git diff a.py", str(repo)) is None
        # …and an untouched family in the same repo still rewrites.
        assert decide("ls -la && ls -R", str(repo)) is not None


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
    def test_default_is_allow(self, repo) -> None:
        result = decide("pytest -x", str(repo))
        assert result is not None
        assert result.command == "repowise distill --source hook-bash pytest -x"
        assert result.permission == "allow"
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

    def test_family_setting_overrides_default(self, repo) -> None:
        # A per-family `ask` overrides the default `allow`; an unlisted family
        # keeps the default.
        _write_config(repo, {"commands": {"families": {"test_output": "ask"}}})
        assert decide("git status", str(repo)).permission == "allow"
        assert decide("pytest -x", str(repo)).permission == "ask"

    def test_global_ask_posture(self, repo) -> None:
        _write_config(repo, {"commands": {"permission": "ask"}})
        assert decide("git status", str(repo)).permission == "ask"

    def test_family_off_disables_one_family(self, repo) -> None:
        _write_config(repo, {"commands": {"families": {"git_diff": "off"}}})
        assert decide("git diff main", str(repo)) is None
        assert decide("git status", str(repo)) is not None

    def test_family_deny_alias(self, repo) -> None:
        _write_config(repo, {"commands": {"families": {"git_diff": "deny"}}})
        assert decide("git diff main", str(repo)) is None

    def test_malformed_config_defaults_to_allow(self, repo) -> None:
        (repo / ".repowise" / "config.yaml").write_text(
            "distill: [not, a, mapping", encoding="utf-8"
        )
        result = decide("pytest -x", str(repo))
        assert result is not None and result.permission == "allow"


class TestDecidePowerShell:
    """shell="powershell" — PS aliases never rewritten, neutral commands are."""

    @pytest.mark.parametrize(
        "command",
        [
            "ls",  # Get-ChildItem alias; not the unix binary
            "cat server.log",  # Get-Content alias
            "tail -n 100 app.log",
            "find . -name '*.py'",  # Windows find.exe differs
            "tree src",  # tree.com differs
            "grep -rn auth .",
        ],
    )
    def test_ps_alias_tokens_pass_through(self, repo, command) -> None:
        assert decide(command, str(repo), shell="powershell") is None
        # The same command from a POSIX shell still rewrites.
        assert decide(command, str(repo), shell="posix") is not None

    @pytest.mark.parametrize(
        "command",
        [
            "git status",
            "git log --oneline -20",
            "pytest tests/unit -q",
            r".venv\Scripts\pytest.exe -q",
            "npm run build",
            "cargo test",
        ],
    )
    def test_shell_neutral_commands_rewrite(self, repo, command) -> None:
        result = decide(command, str(repo), shell="powershell")
        assert result is not None
        assert result.command == f"repowise distill --source hook-powershell {command}"
        assert result.permission == "allow"


# ---------------------------------------------------------------------------
# End-to-end: PreToolUse payload in, hookSpecificOutput JSON out
# ---------------------------------------------------------------------------


def _run_main(monkeypatch, payload, argv: list[str] | None = None) -> str:
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "argv", ["repowise-rewrite", *(argv or [])])
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
        assert hso["permissionDecision"] == "allow"
        assert hso["updatedInput"] == {"command": "repowise distill --source hook-bash pytest -x"}
        assert hso["permissionDecisionReason"]

    def test_passthrough_emits_nothing(self, monkeypatch, repo) -> None:
        assert _run_main(monkeypatch, _payload("cd src", str(repo))) == ""

    def test_non_shell_tool_ignored(self, monkeypatch, repo) -> None:
        assert _run_main(monkeypatch, _payload("pytest", str(repo), tool_name="Grep")) == ""

    def test_powershell_tool_rewrites(self, monkeypatch, repo) -> None:
        out = _run_main(monkeypatch, _payload("git status", str(repo), tool_name="PowerShell"))
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["updatedInput"] == {
            "command": "repowise distill --source hook-powershell git status"
        }
        assert hso["permissionDecision"] == "allow"

    def test_powershell_alias_passes_through(self, monkeypatch, repo) -> None:
        assert _run_main(monkeypatch, _payload("ls -la", str(repo), tool_name="PowerShell")) == ""

    def test_post_tool_use_ignored(self, monkeypatch, repo) -> None:
        payload = _payload("pytest", str(repo), hook_event_name="PostToolUse")
        assert _run_main(monkeypatch, payload) == ""

    def test_malformed_json_is_silent(self, monkeypatch) -> None:
        assert _run_main(monkeypatch, "{not json") == ""

    def test_empty_stdin_is_silent(self, monkeypatch) -> None:
        assert _run_main(monkeypatch, "") == ""


class TestMainCodex:
    """--agent codex: rewrite only what Codex's protocol can honor.

    Codex PreToolUse hooks honor ``updatedInput`` only with
    ``permissionDecision: "allow"`` — there is no ask-with-mutation. An
    ``ask`` decision therefore passes through instead of silently escalating.
    """

    def test_ask_family_passes_through(self, monkeypatch, repo) -> None:
        # An explicit `ask` posture → Codex has no ask-with-mutation, so the
        # command runs raw instead of silently escalating to an unprompted
        # rewrite.
        _write_config(repo, {"commands": {"permission": "ask"}})
        out = _run_main(monkeypatch, _payload("pytest -x", str(repo)), argv=["--agent", "codex"])
        assert out == ""

    def test_default_allow_rewrites_for_codex(self, monkeypatch, repo) -> None:
        # The default `allow` posture is honorable by Codex, so a plain repo
        # rewrites without any config.
        out = _run_main(monkeypatch, _payload("pytest -x", str(repo)), argv=["--agent", "codex"])
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["permissionDecision"] == "allow"
        assert hso["updatedInput"] == {"command": "repowise distill --source hook-codex pytest -x"}

    def test_allow_family_rewrites(self, monkeypatch, repo) -> None:
        _write_config(repo, {"commands": {"families": {"test_output": "allow"}}})
        out = _run_main(monkeypatch, _payload("pytest -x", str(repo)), argv=["--agent", "codex"])
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["permissionDecision"] == "allow"
        assert hso["updatedInput"] == {"command": "repowise distill --source hook-codex pytest -x"}

    def test_agent_equals_form(self, monkeypatch, repo) -> None:
        _write_config(repo, {"commands": {"permission": "allow"}})
        out = _run_main(monkeypatch, _payload("git status", str(repo)), argv=["--agent=codex"])
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["updatedInput"] == {"command": "repowise distill --source hook-codex git status"}

    def test_powershell_tool_name_rejected(self, monkeypatch, repo) -> None:
        # Codex has no PowerShell tool; a payload claiming one is malformed.
        _write_config(repo, {"commands": {"permission": "allow"}})
        payload = _payload("git status", str(repo), tool_name="PowerShell")
        assert _run_main(monkeypatch, payload, argv=["--agent", "codex"]) == ""

    def test_unknown_agent_falls_back_to_claude_code(self, monkeypatch, repo) -> None:
        out = _run_main(monkeypatch, _payload("pytest -x", str(repo)), argv=["--agent", "weird"])
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestNonRepoRoots:
    """~ and the system temp ROOT never count as repo opt-ins, even when a
    stray .repowise exists there; repos created UNDER them still match."""

    def test_temp_root_repowise_is_ignored(self, tmp_path, monkeypatch) -> None:
        fake_tmp = tmp_path / "faketmp"
        (fake_tmp / ".repowise").mkdir(parents=True)
        cwd = fake_tmp / "work" / "sub"
        cwd.mkdir(parents=True)
        monkeypatch.setattr(rewrite_hook.tempfile, "gettempdir", lambda: str(fake_tmp))
        # Ancestors of tmp_path on the host may legitimately match, so pin
        # only the guard: the walk must never return the temp root itself.
        import os

        found = rewrite_hook._find_repo_root(str(cwd))
        assert found is None or os.path.realpath(found) != os.path.realpath(str(fake_tmp))

    def test_repo_under_temp_root_still_matches(self, tmp_path, monkeypatch) -> None:
        fake_tmp = tmp_path / "faketmp"
        repo = fake_tmp / "checkout"
        (repo / ".repowise").mkdir(parents=True)
        monkeypatch.setattr(rewrite_hook.tempfile, "gettempdir", lambda: str(fake_tmp))
        import os

        found = rewrite_hook._find_repo_root(str(repo))
        assert found is not None
        assert os.path.realpath(found) == os.path.realpath(str(repo))
