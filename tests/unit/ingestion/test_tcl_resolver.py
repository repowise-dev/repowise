"""Unit tests for the Tcl ``source`` / ``package require`` import resolver.

Parser contract: the whole ``source`` argument list arrives as written
(``-echo -verbose "x.tcl"``, ``$dir/x.tcl``); the resolver drops options and
quotes itself.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from repowise.core.ingestion.models import Import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.tcl import resolve_tcl_import, resolve_tcl_statement


def _ctx(paths: set[str], sources: dict[str, bytes] | None = None) -> ResolverContext:
    return ResolverContext(
        path_set=set(paths), stem_map={}, graph=nx.DiGraph(), source_map=sources or {}
    )


class TestSource:
    def test_importer_relative(self) -> None:
        ctx = _ctx({"flow/run.tcl", "flow/lib/x.tcl"})
        assert resolve_tcl_import("lib/x.tcl", "flow/run.tcl", ctx) == "flow/lib/x.tcl"

    def test_repo_root_relative(self) -> None:
        ctx = _ctx({"flow/run.tcl", "scripts/x.tcl"})
        assert resolve_tcl_import("scripts/x.tcl", "flow/run.tcl", ctx) == "scripts/x.tcl"

    def test_options_and_quotes_are_dropped(self) -> None:
        ctx = _ctx({"flow/run.tcl", "flow/setup.tcl"})
        assert resolve_tcl_import('-echo -verbose "setup.tcl"', "flow/run.tcl", ctx) == (
            "flow/setup.tcl"
        )

    def test_encoding_option_takes_a_value(self) -> None:
        ctx = _ctx({"flow/run.tcl", "flow/lib/x.tcl"})
        assert resolve_tcl_import("-encoding utf-8 lib/x.tcl", "flow/run.tcl", ctx) == (
            "flow/lib/x.tcl"
        )

    def test_script_dir_anchor(self) -> None:
        ctx = _ctx({"flow/run.tcl", "flow/lib/x.tcl"})
        for anchor in ("$dir", "${dir}", "$env(ROOT)", "[file dirname [info script]]"):
            assert resolve_tcl_import(f"{anchor}/lib/x.tcl", "flow/run.tcl", ctx) == (
                "flow/lib/x.tcl"
            )

    def test_globally_qualified_variable_anchor(self) -> None:
        ctx = _ctx({"flow/run.tcl", "flow/lib/x.tcl", "tools/common/procs.tcl"})
        assert resolve_tcl_import("$::dir/lib/x.tcl", "flow/run.tcl", ctx) == "flow/lib/x.tcl"
        assert resolve_tcl_import("$::env(ROOT)/common/procs.tcl", "flow/run.tcl", ctx) == (
            "tools/common/procs.tcl"
        )

    def test_root_anchor_by_unique_suffix(self) -> None:
        ctx = _ctx({"flow/run.tcl", "tools/common/procs.tcl"})
        assert resolve_tcl_import("$ROOT/common/procs.tcl", "flow/run.tcl", ctx) == (
            "tools/common/procs.tcl"
        )

    def test_ambiguous_suffix_is_external(self) -> None:
        ctx = _ctx({"flow/run.tcl", "a/common/p.tcl", "b/common/p.tcl"})
        assert resolve_tcl_import("$ROOT/common/p.tcl", "flow/run.tcl", ctx) == (
            "external:$ROOT/common/p.tcl"
        )

    def test_bare_filename_after_anchor_is_not_suffix_matched(self) -> None:
        ctx = _ctx({"flow/run.tcl", "elsewhere/p.tcl"})
        assert resolve_tcl_import("$ROOT/p.tcl", "flow/run.tcl", ctx) == "external:$ROOT/p.tcl"

    def test_interpolated_or_absolute_is_external(self) -> None:
        ctx = _ctx({"flow/run.tcl"})
        assert resolve_tcl_import("lib/$name.tcl", "flow/run.tcl", ctx) == (
            "external:lib/$name.tcl"
        )
        assert resolve_tcl_import("/opt/tools/x.tcl", "flow/run.tcl", ctx) == (
            "external:/opt/tools/x.tcl"
        )


class TestPackages:
    def test_require_links_to_single_provider(self) -> None:
        ctx = _ctx(
            {"flow/run.tcl", "lib/util.tcl"},
            {"lib/util.tcl": b"package provide util 1.0\nproc u {} {}\n"},
        )
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "lib/util.tcl"

    def test_two_providers_link_nothing(self) -> None:
        ctx = _ctx(
            {"flow/run.tcl", "a/u.tcl", "b/u.tcl"},
            {"a/u.tcl": b"package provide util 1.0\n", "b/u.tcl": b"package provide util 2.0\n"},
        )
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "external:util"

    def test_providers_are_commands_not_text(self) -> None:
        ctx = _ctx(
            {"flow/run.tcl", "lib/util.tcl", "lib/help.tcl"},
            {
                "lib/util.tcl": b"namespace eval util { package provide util 1.0 }\n",
                "lib/help.tcl": b"set help {\npackage provide util 1.0\n}\n",
            },
        )
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "lib/util.tcl"

    def test_a_switch_arm_is_not_a_provider(self) -> None:
        ctx = _ctx(
            {"flow/run.tcl", "lib/util.tcl", "lib/mode.tcl"},
            {
                "lib/util.tcl": b"package provide util 1.0\n",
                "lib/mode.tcl": b"switch $mode {\npackage provide util 1.0\n}\n",
            },
        )
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "lib/util.tcl"

    def test_a_quoted_or_braced_provider_name_is_unwrapped(self) -> None:
        ctx = _ctx(
            {"flow/run.tcl", "lib/a.tcl", "lib/b.tcl"},
            {
                "lib/a.tcl": b'package provide "util" 1.0\n',
                "lib/b.tcl": b"package provide {gen} 1.0\n",
            },
        )
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "lib/a.tcl"
        assert resolve_tcl_import("gen", "flow/run.tcl", ctx) == "lib/b.tcl"

    def test_a_version_query_is_not_a_provider(self) -> None:
        ctx = _ctx(
            {"flow/run.tcl", "lib/util.tcl", "lib/check.tcl"},
            {
                "lib/util.tcl": b"package provide util 1.0\n",
                "lib/check.tcl": b"package provide util\n",
            },
        )
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "lib/util.tcl"

    def test_one_file_providing_twice_is_one_provider(self) -> None:
        src = b"if {$a} {\n  package provide util 1.0\n} else {\n  package provide util 1.0\n}\n"
        ctx = _ctx({"flow/run.tcl", "lib/util.tcl"}, {"lib/util.tcl": src})
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "lib/util.tcl"

    def test_providers_named_without_lowercase_tcl(self) -> None:
        tcl = SimpleNamespace(file_info=SimpleNamespace(language="tcl"))
        ctx = _ctx(
            {"flow/run.tcl", "lib/util", "lib/GEN.TCL"},
            {
                "lib/util": b"#!/usr/bin/env tclsh\npackage provide util 1.0\n",
                "lib/GEN.TCL": b"package provide gen 1.0\n",
            },
        )
        ctx.parsed_files = {"lib/util": tcl}
        assert resolve_tcl_import("util", "flow/run.tcl", ctx) == "lib/util"
        assert resolve_tcl_import("gen", "flow/run.tcl", ctx) == "lib/GEN.TCL"

    def test_provider_read_from_disk(self, tmp_path: Path) -> None:
        (tmp_path / "lib").mkdir()
        (tmp_path / "lib" / "util.tcl").write_bytes(b"package provide util 1.0\n")
        ctx = ResolverContext(
            path_set={"run.tcl", "lib/util.tcl"},
            stem_map={},
            graph=nx.DiGraph(),
            repo_path=tmp_path,
        )
        assert resolve_tcl_import("util", "run.tcl", ctx) == "lib/util.tcl"

    def test_tcl_and_tk_produce_no_node(self) -> None:
        ctx = _ctx({"run.tcl"})
        assert resolve_tcl_import("Tcl", "run.tcl", ctx) is None
        assert resolve_tcl_import("Tk", "run.tcl", ctx) is None

    def test_unknown_package_is_external(self) -> None:
        ctx = _ctx({"run.tcl"})
        assert resolve_tcl_import("http", "run.tcl", ctx) == "external:http"


def _statement(raw: str, module: str) -> Import:
    return Import(
        raw_statement=raw,
        module_path=module,
        imported_names=[],
        is_relative=False,
        resolved_file=None,
    )


class TestCommandKind:
    def test_source_of_an_extensionless_file_is_a_path(self) -> None:
        ctx = _ctx({"flow/run.tcl", "flow/setup"})
        imp = _statement("source setup", "setup")
        assert resolve_tcl_statement(imp, "flow/run.tcl", ctx) == "flow/setup"

    def test_package_require_never_names_a_file(self) -> None:
        ctx = _ctx({"flow/run.tcl", "flow/x.tcl"})
        imp = _statement("package require x.tcl", "x.tcl")
        assert resolve_tcl_statement(imp, "flow/run.tcl", ctx) == "external:x.tcl"
