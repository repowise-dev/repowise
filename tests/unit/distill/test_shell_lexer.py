"""The shared shell lexer behind the rewrite hook's bailouts.

The hook fires before every agent shell command, so this module carries two
contracts: it must describe command structure correctly (quoting, operators,
redirects, pipeline stages) and it must stay stdlib-only. The structural
cases live here; the import-graph guard lives in ``test_rewrite_perf``.
"""

from __future__ import annotations

import pytest

from repowise.cli import rewrite_hook
from repowise.cli.shell_lexer import (
    SAFE_FINAL_TOOLS,
    analyze_pipeline,
    render,
    tokenize,
)


def _kinds(command: str) -> list[tuple[str, str]]:
    return [(t.kind, t.text) for t in tokenize(command)]


class TestTokenize:
    def test_plain_command(self) -> None:
        assert _kinds("pytest -q") == [("arg", "pytest"), ("arg", "-q")]

    def test_runs_of_whitespace_collapse(self) -> None:
        assert render(tokenize("  pytest \t -q  ")) == "pytest -q"

    @pytest.mark.parametrize(
        ("command", "last_arg"),
        [
            ('git commit -m "fix a|b"', '"fix a|b"'),
            ("git commit -m 'a && b'", "'a && b'"),
            ('pytest -k "a b"', '"a b"'),
        ],
    )
    def test_operators_inside_quotes_stay_in_the_argument(self, command, last_arg) -> None:
        tokens = tokenize(command)
        assert all(t.kind == "arg" for t in tokens)
        assert tokens[-1].text == last_arg

    def test_escaped_quote_does_not_end_the_run(self) -> None:
        assert _kinds(r'echo "a\"b"') == [("arg", "echo"), ("arg", r'"a\"b"')]

    @pytest.mark.parametrize("command", ['echo "a | b', "echo 'a ; b", 'pytest -k "a; rm -rf /'])
    def test_unterminated_quote_is_an_operator(self, command) -> None:
        """Everything after an unterminated quote is unparseable.

        Consuming it into one argument would let a real ``;`` or ``|`` hide
        there, so the quote itself becomes an operator and callers bail.
        """
        assert any(t.kind == "op" for t in tokenize(command))
        assert analyze_pipeline(command) is None

    @pytest.mark.parametrize(
        ("command", "operator"),
        [
            ("a && b", "&&"),
            ("a || b", "||"),
            ("a ; b", ";"),
            ("a &", "&"),
            ("echo `date`", "`"),
            ("cat $(cfg)", "$("),
            ("pytest -x\ngit status", "\n"),
            ("pytest -x\r\ngit status", "\n"),
            ("pytest \\\n  -x", "\\\n"),  # line continuation is still multi-line
        ],
    )
    def test_operators_are_their_own_tokens(self, command, operator) -> None:
        assert any(t.kind == "op" and t.text == operator for t in tokenize(command))

    def test_crlf_is_one_operator(self) -> None:
        assert [t.text for t in tokenize("a\r\nb") if t.kind == "op"] == ["\n"]

    def test_pipe_kinds(self) -> None:
        assert [t.text for t in tokenize("a | b") if t.kind == "pipe"] == ["|"]
        assert [t.text for t in tokenize("a |& b") if t.kind == "pipe"] == ["|&"]


class TestRedirects:
    """A leading fd digit belongs to the redirect, not to the command.

    Without this, ``cargo test 2>&1`` re-renders as ``cargo test 2 >&1`` —
    a different command that fails when it runs.
    """

    @pytest.mark.parametrize(
        ("command", "redirect"),
        [
            ("cargo test 2>&1", "2>&1"),
            ("pytest > out.txt", ">"),
            ("pytest >> out.txt", ">>"),
            ("pytest < input.txt", "<"),
            ("make 2>/dev/null", "2>"),
        ],
    )
    def test_fd_digit_binds_to_the_redirect(self, command, redirect) -> None:
        redirects = [t.text for t in tokenize(command) if t.kind == "redirect"]
        assert redirects == [redirect]

    @pytest.mark.parametrize(
        "command",
        [
            "cargo test 2>&1",
            "pytest -q > out.txt",
            "cargo test 2>&1 | grep fail",
            "pytest --maxfail 2 2>&1",
        ],
    )
    def test_render_round_trips_when_spacing_is_already_canonical(self, command) -> None:
        assert render(tokenize(command)) == command

    @pytest.mark.parametrize(
        ("command", "rendered"),
        [
            ("make 2>/dev/null", "make 2> /dev/null"),
            ("pytest>out", "pytest > out"),
            ("pytest    -q", "pytest -q"),
        ],
    )
    def test_render_normalizes_spacing_without_changing_meaning(self, command, rendered) -> None:
        """render() is not a byte round-trip, and does not claim to be."""
        assert render(tokenize(command)) == rendered

    def test_background_operator_is_not_swallowed_by_a_redirect(self) -> None:
        # `2>&1&` must be a redirect plus the background operator, not one
        # redirect token that happens to end in `&`.
        tokens = tokenize("cargo test 2>&1&")
        assert [(t.kind, t.text) for t in tokens[-2:]] == [("redirect", "2>&1"), ("op", "&")]
        assert analyze_pipeline("cargo test 2>&1&") is None

    def test_analysis_reports_redirects(self) -> None:
        analysis = analyze_pipeline("cargo test 2>&1 | grep fail")
        assert analysis is not None
        assert analysis.producer == "cargo test 2>&1"
        assert analysis.final_tool == "grep"
        assert analysis.redirects == ("2>&1",)


class TestAnalyzePipeline:
    @pytest.mark.parametrize(
        ("command", "producer", "tool"),
        [
            ("pytest -q", "pytest -q", None),
            ('git commit -m "fix a|b"', 'git commit -m "fix a|b"', None),
            ("pytest | tail -20", "pytest", "tail"),
            ("rg TODO | head -5", "rg TODO", "head"),
            ("cargo test | grep -i fail", "cargo test", "grep"),
            ("npm test | egrep -c error", "npm test", "egrep"),
            ("npm test | fgrep warning", "npm test", "fgrep"),
            ("git log --oneline | rg fix", "git log --oneline", "rg"),
            ("pytest | /usr/bin/grep FAIL", "pytest", "grep"),
            # -F means "fixed strings" to grep, so it must NOT be read as the
            # follow flag it is for tail. The check is tool-aware.
            ("pytest | grep -F FAIL", "pytest", "grep"),
        ],
    )
    def test_recognized_shapes(self, command, producer, tool) -> None:
        analysis = analyze_pipeline(command)
        assert analysis is not None
        assert (analysis.producer, analysis.final_tool) == (producer, tool)

    @pytest.mark.parametrize(
        "command",
        [
            "ls && rm -rf x",
            "ls || true",
            "git status; ls",
            "pytest -x &",
            "cat $(cfg)",
            "echo `date`",
            "pytest -x\ngit status",
            "a | b | c",  # 3+ stages
            "make 2>&1 |& grep err",  # stderr pipe
            "npm test | grep -f patterns.txt",  # -f reads a config file
            "npm test | grep --file=patterns.txt",
            "npm test | grep -if patterns.txt",
            "npm test | grep -fpatterns.txt",  # attached value
            "npm test | tail -F",  # follow never closes the pipe
            "npm test | tail --follow",
            "npm test | head -f",
            "pytest | awk '{print $1}'",  # not a bare stdin filter
            "pytest | xargs rm",
            "pytest |",  # nothing after the pipe
            "| head",  # nothing before it
            "",
        ],
    )
    def test_bailouts(self, command) -> None:
        assert analyze_pipeline(command) is None

    def test_safe_final_tools_are_stdin_filters(self) -> None:
        assert sorted(SAFE_FINAL_TOOLS) == ["egrep", "fgrep", "grep", "head", "rg", "tail"]


class TestPowerShellEdgeCases:
    """PowerShell shapes the hook used to reject via an ad-hoc character
    scan now reject structurally, for the reason named in each case."""

    @pytest.mark.parametrize(
        "command",
        [
            "git status; git log --oneline -5",  # statement separator
            "git log --oneline `\n  -20",  # backtick continuation
            "git diff $(git merge-base main HEAD)",  # subexpression
            '& "C:\\Program Files\\Git\\bin\\git.exe" status',  # call operator
            "$env:FOO='1'; pytest -x",  # assignment then separator
            "Get-ChildItem | Select-Object -First 5",  # object pipeline
        ],
    )
    def test_still_bail(self, command) -> None:
        assert analyze_pipeline(command) is None
        assert rewrite_hook.classify(command) is None


class TestSharedWithTheHook:
    """One implementation, not a mirrored copy.

    ``_normalize`` is duplicated in the hook on purpose (importing core's
    router would pull the heavy stack); the lexer needs no such copy because
    it is already stdlib-only, so the hook must use this very object.
    """

    def test_hook_uses_this_module(self) -> None:
        assert rewrite_hook.analyze_pipeline is analyze_pipeline

    def test_module_has_no_repowise_dependencies(self) -> None:
        import inspect

        from repowise.cli import shell_lexer

        source = inspect.getsource(shell_lexer)
        assert "import repowise" not in source
        assert "from repowise" not in source
