"""Unit tests for shell AST extraction: functions, source imports, calls."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples" / "shell"


def _file(path: str = "scripts/run.sh") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="shell",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()


class TestShellSymbols:
    def test_both_function_forms(self, parser: ASTParser) -> None:
        src = b"foo() {\n  echo a\n}\nfunction bar {\n  echo b\n}\n"
        result = parser.parse_file(_file(), src)
        names = {s.name for s in result.symbols}
        assert names == {"foo", "bar"}
        assert all(s.kind == "function" for s in result.symbols)

    def test_function_with_parens_keyword_form(self, parser: ASTParser) -> None:
        src = b"function baz() {\n  echo hi\n}\n"
        result = parser.parse_file(_file(), src)
        assert {s.name for s in result.symbols} == {"baz"}

    def test_zero_functions_no_symbols(self, parser: ASTParser) -> None:
        src = b'#!/bin/sh\necho "hi"\nfor f in *.log; do rm "$f"; done\n'
        result = parser.parse_file(_file(), src)
        assert result.symbols == []


class TestShellImports:
    def test_source_and_dot(self, parser: ASTParser) -> None:
        src = b'source "./lib/util.sh"\n. ./helpers.sh\n'
        result = parser.parse_file(_file(), src)
        modules = [i.module_path for i in result.imports]
        assert modules == ["./lib/util.sh", "./helpers.sh"]

    def test_script_dir_idiom_captured_raw(self, parser: ASTParser) -> None:
        src = b'source "$SCRIPT_DIR/lib/util.sh"\n'
        result = parser.parse_file(_file(), src)
        assert [i.module_path for i in result.imports] == ["$SCRIPT_DIR/lib/util.sh"]

    def test_plain_command_is_not_an_import(self, parser: ASTParser) -> None:
        src = b'echo "not an import"\ngrep foo bar\n'
        result = parser.parse_file(_file(), src)
        assert result.imports == []


class TestShellCalls:
    def test_user_function_call_captured(self, parser: ASTParser) -> None:
        src = b'deploy() {\n  greet "world"\n}\n'
        result = parser.parse_file(_file(), src)
        targets = {c.target_name for c in result.calls}
        assert "greet" in targets

    def test_builtins_are_suppressed(self, parser: ASTParser) -> None:
        src = b"run() {\n  echo hi\n  cd /tmp\n  local x=1\n  exit 0\n}\n"
        result = parser.parse_file(_file(), src)
        targets = {c.target_name for c in result.calls}
        assert not ({"echo", "cd", "local", "exit"} & targets)

    def test_external_binary_captured_but_resolves_to_nothing(self, parser: ASTParser) -> None:
        # grep is captured as a call target; it has no matching function
        # symbol, so the call resolver drops it (verified at the graph level).
        src = b"scan() {\n  grep foo bar\n}\n"
        result = parser.parse_file(_file(), src)
        assert "grep" in {c.target_name for c in result.calls}


class TestShellFixtures:
    def _parse(self, parser: ASTParser, name: str):
        src = (_FIXTURES / name).read_bytes()
        return parser.parse_file(_file(name), src)

    def test_functions_fixture(self, parser: ASTParser) -> None:
        result = self._parse(parser, "functions.sh")
        assert {s.name for s in result.symbols} == {"greet", "deploy", "build_step"}

    def test_sourcing_fixture(self, parser: ASTParser) -> None:
        result = self._parse(parser, "sourcing.sh")
        modules = [i.module_path for i in result.imports]
        assert "./helpers.sh" in modules
        assert "$SCRIPT_DIR/lib/util.sh" in modules
        assert "${BASH_SOURCE%/*}/lib/log.sh" in modules

    def test_control_flow_fixture_walks(self, parser: ASTParser) -> None:
        result = self._parse(parser, "control_flow.sh")
        assert {"process", "trivial"} <= {s.name for s in result.symbols}

    def test_zero_function_fixture(self, parser: ASTParser) -> None:
        result = self._parse(parser, "zero_functions.sh")
        assert result.symbols == []

    def test_zsh_degrades_but_still_extracts(self, parser: ASTParser) -> None:
        # zsh-only constructs may parse imperfectly; extraction must degrade
        # per-file (never crash) and still surface the plain function.
        result = self._parse(parser, "zshisms.zsh")
        assert "setup" in {s.name for s in result.symbols}
