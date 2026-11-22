"""LPC routing, parsing and conservative import graph regressions."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.languages import lpc_support as support
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import parse_file
from repowise.core.ingestion.parser.grammars import _compile_query
from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.traverser import FileTraverser, _detect_language

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/lpc"


@pytest.fixture(autouse=True)
def lpc_config(monkeypatch):
    monkeypatch.delenv("REPOWISE_LPC_CONFIG", raising=False)
    monkeypatch.setenv("REPOWISE_LPC_ROOTS", str(FIXTURE / "mudlib"))


def parsed(path: str):
    absolute = FIXTURE / path
    info = FileInfo(
        path=path, abs_path=str(absolute), language=_detect_language(absolute),
        size_bytes=absolute.stat().st_size, git_hash="", last_modified=datetime.now(),
        is_test=False, is_config=False, is_api_contract=False, is_entry_point=False,
    )
    return parse_file(info, absolute.read_bytes())


def test_query_compiles_against_real_grammar():
    query, error = _compile_query("lpc")
    assert query is not None
    assert error is None


def test_c_is_unchanged_without_explicit_roots(monkeypatch):
    monkeypatch.delenv("REPOWISE_LPC_ROOTS")
    assert _detect_language(FIXTURE / "mudlib/obj/literals.c") == "c"
    assert _detect_language(FIXTURE / "mudlib/sys/paths.h") == "cpp"


def test_mixed_driver_and_mudlib_routing():
    assert _detect_language(FIXTURE / "driver/src/main.c") == "c"
    assert _detect_language(FIXTURE / "mudlib/obj/literals.c") == "lpc"
    assert _detect_language(FIXTURE / "mudlib/sys/paths.h") == "lpc"
    assert not support.is_lpc_path(FIXTURE / "mudlib-extra/room.c")
    assert not support.is_lpc_path(FIXTURE / "mudlib/settings.json")


def test_c_and_headers_outside_roots_still_parse_as_c_and_cpp():
    """Roots are configured (autouse fixture); driver sources beside them keep the C/C++ parsers."""
    source = parsed("driver/src/main.c")
    header = parsed("driver/src/main.h")
    assert (source.file_info.language, header.file_info.language) == ("c", "cpp")
    assert {symbol.name for symbol in source.symbols} >= {"main"}
    assert {symbol.name for symbol in header.symbols} >= {"driver_state", "driver_tick"}
    assert [imp.module_path for imp in source.imports] == ["<stdio.h>"]


def test_lpc_stays_inactive_without_the_grammar(monkeypatch):
    monkeypatch.setattr(support, "grammar_available", lambda: False)
    assert _detect_language(FIXTURE / "mudlib/obj/literals.c") == "c"
    assert _detect_language(FIXTURE / "mudlib/sys/paths.h") == "cpp"
    assert not support.is_lpc_path(FIXTURE / "mudlib/obj/literals.c")


def test_relative_config_roots_and_nested_roots(tmp_path, monkeypatch):
    config = tmp_path / "lpc.json"
    config.write_text(json.dumps({"roots": ["lib", "lib/overlay"], "include": ["sys"]}))
    monkeypatch.setenv("REPOWISE_LPC_CONFIG", str(config))
    monkeypatch.delenv("REPOWISE_LPC_ROOTS")
    assert support.root_for(tmp_path / "lib/overlay/obj/a.c") == tmp_path / "lib/overlay"
    assert support.config().include == ("sys",)
    config.write_text(json.dumps({"roots": ["another"]}))
    assert support.root_for(tmp_path / "lib/obj/a.c") is None


def test_config_is_cached_until_its_inputs_change(tmp_path, monkeypatch):
    config = tmp_path / "lpc.json"
    config.write_text(json.dumps({"roots": ["lib"]}))
    monkeypatch.setenv("REPOWISE_LPC_CONFIG", str(config))
    monkeypatch.setenv("REPOWISE_LPC_ROOTS", "relative")
    monkeypatch.chdir(tmp_path)
    first = support.config()
    assert support.config() is first
    monkeypatch.chdir(tmp_path / "..")
    assert tmp_path / "relative" not in support.config().roots
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPOWISE_LPC_ROOTS", "")
    assert support.config().roots == (tmp_path / "lib",)
    config.write_text(json.dumps({"roots": ["lib", "other"]}))
    assert tmp_path / "other" in support.config().roots


@pytest.mark.parametrize("contents", ['[]', '{"roots":"lib"}', '{"include":[1]}', '{'])
def test_bad_config_is_reported(tmp_path, monkeypatch, contents):
    config = tmp_path / "lpc.json"
    config.write_text(contents)
    monkeypatch.setenv("REPOWISE_LPC_CONFIG", str(config))
    with pytest.raises(ValueError):
        support.config()


def test_missing_config_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("REPOWISE_LPC_CONFIG", str(tmp_path / "missing.json"))
    with pytest.raises(FileNotFoundError):
        support.config()


def test_symlink_outside_root_stays_c(tmp_path, monkeypatch):
    mudlib = tmp_path / "lib"
    mudlib.mkdir()
    outside = tmp_path / "outside.c"
    outside.write_text("int main() { return 0; }")
    (mudlib / "linked.c").symlink_to(outside)
    monkeypatch.setenv("REPOWISE_LPC_ROOTS", str(mudlib))
    assert _detect_language(mudlib / "linked.c") == "c"


def test_symbols_and_visibility():
    result = parsed("mudlib/obj/literals.c")
    assert not result.grammar_missing
    assert not result.parse_errors
    names = {symbol.name: symbol for symbol in result.symbols}
    assert {"counter", "helper", "point", "create"} <= names.keys()
    assert "proto" not in names
    assert names["counter"].visibility == "private"
    assert names["helper"].visibility == "protected"
    assert names["create"].visibility == "public"


def test_named_callbacks_and_closures():
    result = parsed("mudlib/obj/callbacks.c")
    targets = {call.target_name for call in result.calls if not call.receiver_name}
    assert {"do_wave", "tick", "got_name", "by_level", "alive", "later", "closure_fn"} <= targets
    assert "notmine" not in targets
    assert "wave" not in targets


@pytest.mark.parametrize("text,node_type,want", [
    ('"tick"', "string_literal", "tick"),
    ("#'tick", "closure", "tick"),
    ("#'::create", "closure", ""),
    ("#'+", "closure", ""),
    ('"not a name"', "string_literal", ""),
])
def test_call_target_normalization(text, node_type, want):
    assert support.lpc_call_target(text, node_type) == want


def test_import_delimiters_survive_parsing():
    result = parsed("mudlib/obj/literals.c")
    imports = {item.module_path for item in result.imports}
    assert '#include "../sys/paths.h"' in imports
    assert '#include <ansi.h>' in imports
    assert 'inherit "/std/room";' in imports


@pytest.mark.parametrize("statement,want", [
    ('inherit "/std/room";', "mudlib/std/room.c"),
    ('virtual inherit "/std/room.c";', "mudlib/std/room.c"),
    ('#include "../sys/paths.h"', "mudlib/sys/paths.h"),
    ('#include "/sys/paths.h"', "mudlib/sys/paths.h"),
    ('#include <sys/paths.h>', "mudlib/sys/paths.h"),
    ('inherit STD_ROOM;', None),
    ('inherit "/std/" + SUFFIX;', None),
    ('inherit "std/room";', None),
    ('inherit "/missing";', None),
    ('#include UNKNOWN_HEADER', None),
    ('#include "../../driver/src/main.c"', None),
    ('#include <sys/paths.h', None),
])
def test_import_resolution(statement, want):
    target = support.resolve_statement(statement, FIXTURE / "mudlib/obj/literals.c")
    assert (target.relative_to(FIXTURE).as_posix() if target else None) == want


def test_configured_include_directories(tmp_path, monkeypatch):
    config = tmp_path / "lpc.json"
    config.write_text(json.dumps({"roots": [str(FIXTURE / "mudlib")], "include": ["sys"]}))
    monkeypatch.setenv("REPOWISE_LPC_CONFIG", str(config))
    target = support.resolve_statement('#include <ansi.h>', FIXTURE / "mudlib/obj/literals.c")
    assert target == FIXTURE / "mudlib/sys/ansi.h"


def test_import_symlink_cannot_escape_root(tmp_path, monkeypatch):
    mudlib = tmp_path / "lib"
    mudlib.mkdir()
    outside = tmp_path / "outside.h"
    outside.write_text("#define X 1\n")
    (mudlib / "link.h").symlink_to(outside)
    monkeypatch.setenv("REPOWISE_LPC_ROOTS", str(mudlib))
    assert support.resolve_statement('#include "/link.h"', mudlib / "obj.c") is None


def test_roots_are_not_implicitly_overlaid(tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "parent.c").write_text("void create() {}")
    monkeypatch.setenv("REPOWISE_LPC_ROOTS", os.pathsep.join(map(str, [first, second])))
    assert support.resolve_statement('inherit "/parent";', first / "obj.c") is None


def test_resolver_requires_an_indexed_target():
    import networkx as nx

    context = ResolverContext(path_set={"mudlib/obj/literals.c"}, stem_map={}, graph=nx.DiGraph(), repo_path=FIXTURE)
    assert resolve_import('inherit "/std/room";', "mudlib/obj/literals.c", "lpc", context) is None


def test_traversal_parser_and_graph_together():
    traverser = FileTraverser(FIXTURE)
    # This also proves files do not disappear because LPC has no globally claimed suffixes.
    infos = list(traverser.traverse())
    paths = {info.path for info in infos}
    assert {"mudlib/obj/literals.c", "mudlib/std/room.c", "driver/src/main.c"} <= paths
    builder = GraphBuilder(repo_path=FIXTURE)
    for info in infos:
        builder.add_file(parse_file(info, Path(info.abs_path).read_bytes()))
    builder.build()
    graph = builder.graph()
    assert graph.has_edge("mudlib/obj/literals.c", "mudlib/std/room.c")
    assert graph.has_edge("mudlib/obj/literals.c", "mudlib/sys/paths.h")
    assert graph.nodes["driver/src/main.c"]["language"] == "c"


def test_dead_code_claims_are_disabled_for_runtime_dispatched_lpc():
    assert REGISTRY.get("lpc").dead_code_exempt
    assert REGISTRY.import_support_for("lpc") == "partial"


def test_runtime_only_files_and_private_functions_are_not_flagged_dead():
    import networkx as nx

    from repowise.core.analysis.dead_code.analyzer import DeadCodeAnalyzer

    graph = nx.DiGraph()
    graph.add_node("obj/runtime.c", node_type="file", language="lpc",
                   is_entry_point=False, is_test=False, symbol_count=1)
    graph.add_node("obj/runtime.c::callback", node_type="symbol", language="lpc",
                   file_path="obj/runtime.c", name="callback", kind="function",
                   visibility="private", start_line=1, end_line=3)
    graph.add_edge("obj/runtime.c", "obj/runtime.c::callback", edge_type="defines")
    report = DeadCodeAnalyzer(graph, git_meta_map={}).analyze(
        {"detect_zombie_packages": False, "min_confidence": 0.0}
    )
    assert report.findings == []


def test_include_trailing_comment_does_not_change_target():
    info = parsed("mudlib/obj/literals.c").file_info
    result = parse_file(info, b'#include "local.h" // trailing comment\n')
    assert result.imports[0].module_path == '#include "local.h"'
    assert "trailing comment" in result.imports[0].raw_statement
