"""Perl calls that name their package: ``Pkg::Mod->sub(..)`` and ``Pkg::Mod::sub(..)``.

The package is written at the call site and maps to ``Pkg/Mod.pm`` as ``@INC``
would find it, so the edge is no guess. A package the repo does not hold, or a
sub its file does not define, binds nothing rather than a same-named sub elsewhere.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()

_SOURCES = {
    "lib/Desda/Cache.pm": (
        "package Desda::Cache;\n"
        "sub new { my ($c, %a) = @_; return bless {%a}, $c; }\n"
        "sub stamp { return 1; }\n"
        "1;\n"
    ),
    "lib/Desda/Guard.pm": (
        "package Desda::Guard;\nsub new { return bless {}, shift; }\nsub stamp { return 2; }\n1;\n"
    ),
    "t/cache.t": (
        "use Desda::Cache;\n"
        "sub test_cache {\n"
        "  my $c = Desda::Cache->new(x => 1);\n"
        "  Desda::Cache::stamp($c);\n"
        "  Other::Pkg->new();\n"
        "  Desda::Cache::missing();\n"
        "}\n"
    ),
}


def _build(tmp_path: Path):
    builder = GraphBuilder(tmp_path)
    for rel, text in _SOURCES.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        info = FileInfo(
            path=rel,
            abs_path=str(abs_path),
            language="perl",
            size_bytes=len(text),
            git_hash="",
            last_modified=datetime.now(),
            is_test=rel.startswith("t/"),
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        builder.add_file(_PARSER.parse_file(info, text.encode("utf-8")))
    return builder.build()


def _calls_from(graph, caller: str) -> dict[str, str]:
    return {
        v: d.get("resolution_origin")
        for u, v, d in graph.edges(data=True)
        if u == caller and d.get("edge_type") == "calls"
    }


def test_package_qualified_calls_bind_to_the_package_file(tmp_path: Path) -> None:
    calls = _calls_from(_build(tmp_path), "t/cache.t::test_cache")
    assert calls == {
        "lib/Desda/Cache.pm::new": "scoped_name",
        "lib/Desda/Cache.pm::stamp": "scoped_name",
    }


def test_split_keeps_the_package_as_receiver() -> None:
    info = FileInfo(
        path="t/x.t",
        abs_path="/tmp/t/x.t",
        language="perl",
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=True,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    result = _PARSER.parse_file(info, b"sub f {\n  A::B::go(1);\n  helper();\n}\n")
    assert {(c.target_name, c.receiver_name) for c in result.calls} == {
        ("go", "A::B"),
        ("helper", None),
    }
