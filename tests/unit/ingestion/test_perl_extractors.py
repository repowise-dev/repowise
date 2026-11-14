"""Unit tests for Perl AST extraction: subs, use/require imports, calls."""

from __future__ import annotations

from datetime import datetime

import pytest

from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE, FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.test_paths import is_test_path


def _file(path: str = "lib/My/Thing.pm") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=f"/tmp/{path}",
        language="perl",
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


def test_extensions_route_to_perl() -> None:
    for ext in (".pl", ".pm", ".t"):
        assert EXTENSION_TO_LANGUAGE[ext] == "perl"


def test_t_directory_is_test_material() -> None:
    assert is_test_path("t/basic.t", "perl")
    assert is_test_path("t/lib/Helper.pm", "perl")
    assert not is_test_path("lib/My/Thing.pm", "perl")


class TestPerlSymbols:
    def test_named_subs(self, parser: ASTParser) -> None:
        src = b"package A;\nsub one { 1 }\nsub two {\n  return 2;\n}\n1;\n"
        result = parser.parse_file(_file(), src)
        assert [(s.name, s.kind, s.start_line, s.end_line) for s in result.symbols] == [
            ("one", "function", 2, 2),
            ("two", "function", 3, 5),
        ]

    def test_anonymous_sub_is_not_a_symbol(self, parser: ASTParser) -> None:
        result = parser.parse_file(_file(), b"my $cb = sub { 1 };\n")
        assert result.symbols == []


class TestPerlImports:
    def test_use_and_require(self, parser: ASTParser) -> None:
        src = (
            b"use strict;\nuse File::Spec;\nuse parent -norequire, 'Base';\n"
            b'require Some::Module;\nrequire "lib/helper.pl";\n'
        )
        result = parser.parse_file(_file(), src)
        assert [i.module_path for i in result.imports] == [
            "strict",
            "File::Spec",
            "parent",
            "Some::Module",
            "lib/helper.pl",
        ]

    def test_use_version_is_not_an_import(self, parser: ASTParser) -> None:
        result = parser.parse_file(_file(), b"use 5.010;\nuse v5.36;\n")
        assert result.imports == []


class TestPerlCalls:
    def test_call_forms_and_builtins(self, parser: ASTParser) -> None:
        src = (
            b"sub f {\n  helper(1);\n  bare $x;\n  $obj->meth(2);\n"
            b"  Pkg::Mod::fn();\n  print 'x';\n  die 'y' unless $z;\n}\n"
        )
        result = parser.parse_file(_file(), src)
        assert {c.target_name for c in result.calls} == {"helper", "bare", "meth", "fn"}
