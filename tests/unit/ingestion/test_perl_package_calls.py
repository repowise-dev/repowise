"""Perl calls that name their package: ``Pkg::Mod->sub(..)`` and ``Pkg::Mod::sub(..)``.

The package is written at the call site and maps to ``Pkg/Mod.pm`` as ``@INC``
would find it, so the edge is no guess. A package the repo does not hold, or a
sub its file does not define, binds nothing rather than a same-named sub elsewhere.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.test_perl_inheritance import _build as _build_sources
from tests.unit.ingestion.test_perl_inheritance import _calls_from

_PARSER = ASTParser()

_SOURCES = {
    "lib/Acme/Cache.pm": (
        "package Acme::Cache;\n"
        "sub new { my ($c, %a) = @_; return bless {%a}, $c; }\n"
        "sub stamp { return 1; }\n"
        "1;\n"
    ),
    "lib/Acme/Guard.pm": (
        "package Acme::Guard;\nsub new { return bless {}, shift; }\nsub stamp { return 2; }\n1;\n"
    ),
    "t/cache.t": (
        "use Acme::Cache;\n"
        "sub test_cache {\n"
        "  my $c = Acme::Cache->new(x => 1);\n"
        "  Acme::Cache::stamp($c);\n"
        "  Other::Pkg->new();\n"
        "  Acme::Cache::missing();\n"
        "}\n"
    ),
}


def _build(tmp_path: Path):
    return _build_sources(tmp_path, _SOURCES)


def test_package_qualified_calls_bind_to_the_package_file(tmp_path: Path) -> None:
    calls = _calls_from(_build(tmp_path), "t/cache.t::test_cache")
    assert calls == {
        "lib/Acme/Cache.pm::Acme::Cache::new": "scoped_name",
        "lib/Acme/Cache.pm::Acme::Cache::stamp": "scoped_name",
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
    indirect = _PARSER.parse_file(info, b"sub f {\n  my $o = new A::B(x => 1);\n}\n")
    assert [(c.target_name, c.receiver_name) for c in indirect.calls] == [("new", "A::B")]


def test_only_named_or_default_imports_bring_a_sub_into_scope(tmp_path: Path) -> None:
    sources = {
        "lib/Util.pm": "package Util;\nsub helper { 1 }\n1;\n",
        "bin/required.pl": "require Util;\nsub run { helper(); }\n",
        "bin/empty.pl": "use Util ();\nsub run { helper(); }\n",
        "bin/named.pl": "use Util qw(helper);\nsub run { helper(); }\n",
        "bin/default.pl": "use Util;\nsub run { helper(); }\n",
        "bin/none.pl": "sub run { helper(); }\n",
    }
    graph = _build_sources(tmp_path, sources)
    calls = {path: _calls_from(graph, f"{path}::run") for path in sources if path.startswith("bin/")}
    assert calls == {
        "bin/required.pl": {},
        "bin/empty.pl": {},
        "bin/named.pl": {"lib/Util.pm::Util::helper": "import_scoped"},
        "bin/default.pl": {"lib/Util.pm::Util::helper": "import_merged"},
        "bin/none.pl": {},
    }


def test_an_import_binds_only_in_the_package_that_declared_it(tmp_path: Path) -> None:
    sources = {
        "lib/Util.pm": "package Util;\nsub helper { 1 }\n1;\n",
        "lib/Two.pm": (
            "package A;\nuse Util qw(helper);\nsub run { helper(); }\n"
            "package B;\nsub run { helper(); }\n"
        ),
    }
    graph = _build_sources(tmp_path, sources)
    assert _calls_from(graph, "lib/Two.pm::A::run") == {"lib/Util.pm::Util::helper": "import_scoped"}
    assert _calls_from(graph, "lib/Two.pm::B::run") == {}


def test_a_required_library_file_defines_subs_in_main(tmp_path: Path) -> None:
    sources = {
        "lib/helpers.pl": "sub tool { 1 }\n1;\n",
        "bin/run.pl": (
            'require "lib/helpers.pl";\nsub main_run { tool(); }\n'
            "package A;\nsub run { tool(); }\n"
        ),
    }
    graph = _build_sources(tmp_path, sources)
    assert _calls_from(graph, "bin/run.pl::main_run") == {"lib/helpers.pl::tool": "import_scoped"}
    assert _calls_from(graph, "bin/run.pl::A::run") == {}


def test_a_package_no_file_declares_is_not_found_by_file_name(tmp_path: Path) -> None:
    sources = {
        "lib/Foo.pm": "sub helper { 1 }\n1;\n",
        "bin/run.pl": "sub run { Foo::helper(); Foo->helper(); }\n",
    }
    assert _calls_from(_build_sources(tmp_path, sources), "bin/run.pl::run") == {}


def test_the_last_import_of_a_name_wins(tmp_path: Path) -> None:
    sources = {
        "lib/A.pm": "package A;\nsub helper { 1 }\n1;\n",
        "lib/B.pm": "package B;\nsub helper { 2 }\n1;\n",
        "bin/run.pl": "use A qw(helper);\nuse B qw(helper);\nsub run { helper(); }\n",
    }
    assert _calls_from(_build_sources(tmp_path, sources), "bin/run.pl::run") == {
        "lib/B.pm::B::helper": "import_scoped"
    }


def test_a_wildcard_import_binds_only_the_modules_own_subs(tmp_path: Path) -> None:
    sources = {
        "lib/Foo.pm": "sub helper { 0 }\npackage Foo;\nsub other { 1 }\n1;\n",
        "bin/run.pl": "package App;\nuse Foo;\nsub run { helper(); other(); }\n",
    }
    assert _calls_from(_build_sources(tmp_path, sources), "bin/run.pl::App::run") == {
        "lib/Foo.pm::Foo::other": "import_merged"
    }


def test_a_lowercase_namespaced_module_is_not_a_pragma(tmp_path: Path) -> None:
    sources = {
        "lib/myapp/util.pm": "package myapp::util;\nsub helper { 1 }\n1;\n",
        "bin/run.pl": "use myapp::util qw(helper);\nsub run { helper(); }\n",
    }
    assert _calls_from(_build_sources(tmp_path, sources), "bin/run.pl::run") == {
        "lib/myapp/util.pm::myapp::util::helper": "import_scoped"
    }


def test_a_later_external_named_import_rebinds_the_name(tmp_path: Path) -> None:
    sources = {
        "lib/Local.pm": "package Local;\nsub sum { 1 }\n1;\n",
        "bin/run.pl": "use Local qw(sum);\nuse List::Util qw(sum);\nsub run { sum(); }\n",
    }
    assert _calls_from(_build_sources(tmp_path, sources), "bin/run.pl::run") == {}


def test_an_import_binds_only_from_a_file_declaring_the_package(tmp_path: Path) -> None:
    sources = {
        "lib/Foo.pm": "package Bar;\nsub helper { 1 }\n1;\n",
        "lib/Other.pm": "package Foo;\nsub helper { 2 }\n1;\n",
        "bin/run.pl": "use Foo qw(helper);\nsub run { helper(); }\n",
    }
    assert _calls_from(_build_sources(tmp_path, sources), "bin/run.pl::run") == {}


def test_a_later_external_default_import_may_rebind_the_name(tmp_path: Path) -> None:
    sources = {
        "lib/Local.pm": "package Local;\nsub croak { 1 }\nsub helper { 2 }\n1;\n",
        "bin/carp.pl": "use Local qw(croak);\nuse Carp;\nsub run { croak(); }\n",
        "bin/pragma.pl": "use Local qw(helper);\nuse strict;\nuse warnings;\nsub run { helper(); }\n",
    }
    graph = _build_sources(tmp_path, sources)
    assert _calls_from(graph, "bin/carp.pl::run") == {}
    assert _calls_from(graph, "bin/pragma.pl::run") == {"lib/Local.pm::Local::helper": "import_scoped"}


def test_an_indirect_constructor_calls_the_package_new(tmp_path: Path) -> None:
    sources = {
        "lib/Acme/Pkg.pm": "package Acme::Pkg;\nsub new { return bless {}, shift; }\n1;\n",
        "bin/run.pl": (
            "use Acme::Pkg;\n"
            "sub new { 0 }\n"
            "sub run {\n  my $o = new Acme::Pkg(x => 1);\n  my $p = new Acme::Pkg;\n}\n"
        ),
    }
    calls = _calls_from(_build_sources(tmp_path, sources), "bin/run.pl::run")
    assert set(calls) == {"lib/Acme/Pkg.pm::Acme::Pkg::new"}


def test_a_recursive_call_binds_the_caller_not_an_import(tmp_path: Path) -> None:
    sources = {
        "lib/Util.pm": "package Util;\nsub helper { 1 }\n1;\n",
        "lib/App.pm": "package App;\nuse Util qw(helper);\nsub helper { helper(); }\n1;\n",
        "lib/Obj.pm": "package Obj;\nsub walk { my $self = shift; $self->walk(); }\n1;\n",
    }
    graph = _build_sources(tmp_path, sources)
    assert _calls_from(graph, "lib/App.pm::App::helper") == {
        "lib/App.pm::App::helper": "enclosing_class"
    }
    assert _calls_from(graph, "lib/Obj.pm::Obj::walk") == {"lib/Obj.pm::Obj::walk": "self_scope"}
