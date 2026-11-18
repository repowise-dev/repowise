"""Perl method calls dispatched through ``@ISA``, as Perl's default method order does.

``$self->m()`` starts at the package around the call, ``SUPER::m`` at its
parents and ``Pkg->m()`` at ``Pkg``; ``Pkg::m()`` is a plain sub call and is
never dispatched. Parents come from ``use parent``, ``use base`` and ``@ISA``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.languages.perl_packages import (
    PerlPackageIndex,
    scan_perl_packages,
)
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()

_SOURCES = {
    "lib/Shape/Base.pm": (
        "package Shape::Base;\n"
        "sub new { my $class = shift; return bless {}, $class; }\n"
        "sub draw { my $self = shift; $self->outline(); }\n"
        "sub outline { 1 }\n"
        "sub area { 0 }\n"
        "1;\n"
    ),
    "lib/Shape/Named.pm": (
        "package Shape::Named;\nuse parent -norequire, 'Shape::Base';\nsub label { 'n' }\n1;\n"
    ),
    "lib/Shape/Circle.pm": (
        "package Shape::Circle;\n"
        "use base qw(Shape::Named);\n"
        "sub area {\n"
        "  my $this = shift;\n"
        "  $this->SUPER::area();\n"
        "  $this->label();\n"
        "  $this->outline();\n"
        "  __PACKAGE__->radius();\n"
        "}\n"
        "sub radius { 2 }\n"
        "sub make { return shift->new(); }\n"
        "sub clone { my $this = shift; return ref($this)->new(); }\n"
        "1;\n"
    ),
    "lib/Shape/Square.pm": (
        "package Shape::Square;\n"
        "our @ISA = ('Shape::Base');\n"
        "sub area { my $self = shift; return $self->side() ** 2; }\n"
        "sub side { 3 }\n"
        "1;\n"
        "__END__\n"
    ),
    "t/shapes.t": (
        "use Shape::Circle;\n"
        "sub test_shapes {\n"
        "  my $c = Shape::Circle->new();\n"
        "  Shape::Circle->draw();\n"
        "  Shape::Circle::draw();\n"
        "  $c->area();\n"
        "  my $s = new Shape::Square();\n"
        "  $s->outline();\n"
        "  $other->area();\n"
        "}\n"
    ),
}


def _build(
    tmp_path: Path,
    sources: dict[str, str] = _SOURCES,
    on_disk: dict[str, str] | None = None,
):
    """Parse *sources*; *on_disk*, when given, is what the working tree holds instead."""
    builder = GraphBuilder(tmp_path)
    if on_disk is not None:
        builder.set_source_map({rel: text.encode("utf-8") for rel, text in sources.items()})
    for rel, text in sources.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text((on_disk or {}).get(rel, text), encoding="utf-8")
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


def test_self_calls_walk_the_parents_depth_first(tmp_path: Path) -> None:
    calls = _calls_from(_build(tmp_path), "lib/Shape/Circle.pm::Shape::Circle::area")
    assert calls == {
        "lib/Shape/Base.pm::Shape::Base::area": "self_inherited",
        "lib/Shape/Named.pm::Shape::Named::label": "self_inherited",
        "lib/Shape/Base.pm::Shape::Base::outline": "self_inherited",
        "lib/Shape/Circle.pm::Shape::Circle::radius": "self_scope",
    }


def test_own_package_and_isa_assignment(tmp_path: Path) -> None:
    graph = _build(tmp_path)
    assert _calls_from(graph, "lib/Shape/Square.pm::Shape::Square::area") == {
        "lib/Shape/Square.pm::Shape::Square::side": "self_scope"
    }
    assert _calls_from(graph, "lib/Shape/Base.pm::Shape::Base::draw") == {
        "lib/Shape/Base.pm::Shape::Base::outline": "self_scope"
    }
    assert _calls_from(graph, "lib/Shape/Circle.pm::Shape::Circle::make") == {
        "lib/Shape/Base.pm::Shape::Base::new": "self_inherited"
    }
    assert _calls_from(graph, "lib/Shape/Circle.pm::Shape::Circle::clone") == {
        "lib/Shape/Base.pm::Shape::Base::new": "self_inherited"
    }


def test_package_receivers_dispatch_but_sub_calls_do_not(tmp_path: Path) -> None:
    calls = _calls_from(_build(tmp_path), "t/shapes.t::test_shapes")
    # ``Shape::Circle::draw()`` names a sub Circle does not define; ``$other`` is untyped.
    assert calls == {
        "lib/Shape/Base.pm::Shape::Base::new": "self_inherited",
        "lib/Shape/Base.pm::Shape::Base::draw": "self_inherited",
        "lib/Shape/Circle.pm::Shape::Circle::area": "receiver_typed_global",
        "lib/Shape/Base.pm::Shape::Base::outline": "receiver_typed_global",
    }


def test_a_package_two_files_declare_binds_nothing(tmp_path: Path) -> None:
    sources = {
        **_SOURCES,
        "legacy/Shape/Base.pm": "package Shape::Base;\nsub outline { 9 }\n1;\n",
    }
    calls = _calls_from(_build(tmp_path, sources), "lib/Shape/Circle.pm::Shape::Circle::area")
    assert "lib/Shape/Base.pm::Shape::Base::outline" not in calls
    assert "legacy/Shape/Base.pm::Shape::Base::outline" not in calls


def test_a_copied_package_binds_its_own_file(tmp_path: Path) -> None:
    sources = {
        **_SOURCES,
        "legacy/Shape/Square.pm": "package Shape::Square;\nsub area { $_[0]->side() }\nsub side { 4 }\n1;\n",
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "legacy/Shape/Square.pm::Shape::Square::area") == {
        "legacy/Shape/Square.pm::Shape::Square::side": "self_scope"
    }
    assert _calls_from(graph, "lib/Shape/Square.pm::Shape::Square::area") == {
        "lib/Shape/Square.pm::Shape::Square::side": "self_scope"
    }


def test_scan_reads_parents_from_code_only() -> None:
    source = (
        b"package A;\n"
        b"use parent -norequire, 'P1', \"P2\";\n"
        b"push @ISA, 'P3';\n"
        b"package B { our @ISA = qw(A P4); sub x { 1 } }\n"
        b"\n=pod\n\nuse base 'Doc::Only';\n\n=cut\n"
    )
    packages = {p.name: p for p in scan_perl_packages(source)}
    assert packages["A"].parents == ("P1", "P2", "P3")
    assert packages["B"].parents == ("A", "P4")
    assert (packages["B"].start_line, packages["B"].end_line) == (4, 4)


def test_index_orders_parents_depth_first_and_stops_on_cycles() -> None:
    source = (
        b"package C; our @ISA = ('B1', 'B2');\n"
        b"package B1; our @ISA = ('A');\n"
        b"package B2; our @ISA = ('A');\n"
        b"package A; our @ISA = ('C');\n"
    )
    index = PerlPackageIndex.build([("x.pm", source)])
    assert index.mro("C") == ("C", "B1", "A", "B2")
    third_line = source.index(b"package B2")
    assert index.package_at("x.pm", third_line) == "B2"


def test_same_named_subs_in_one_file_bind_to_their_own_package(tmp_path: Path) -> None:
    sources = {
        "lib/Shapes.pm": (
            "package Shape::Circle;\n"
            "use parent -norequire, 'Shape::Base';\n"
            "sub new { bless {}, shift }\n"
            "sub helper { 1 }\n"
            "sub area { helper(); }\n"
            "package Shape::Base;\n"
            "sub new { bless {}, shift }\n"
            "sub helper { 2 }\n"
            "sub area { helper(); }\n"
            "package main;\n"
            "sub run { Shape::Circle->new(); Shape::Base->new(); }\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "lib/Shapes.pm::run") == {
        "lib/Shapes.pm::Shape::Circle::new": "scoped_name",
        "lib/Shapes.pm::Shape::Base::new": "scoped_name",
    }
    assert _calls_from(graph, "lib/Shapes.pm::Shape::Circle::area") == {
        "lib/Shapes.pm::Shape::Circle::helper": "enclosing_class"
    }
    assert _calls_from(graph, "lib/Shapes.pm::Shape::Base::area") == {
        "lib/Shapes.pm::Shape::Base::helper": "enclosing_class"
    }


def test_isa_writes_replace_prepend_append_and_clear() -> None:
    source = (
        b"package A;\nuse parent -norequire, 'Old';\nour @ISA = ('New');\n"
        b"package B;\nour @ISA = ('X');\nunshift @ISA, 'Y';\npush @ISA, 'Z';\n"
        b"package C;\nuse parent -norequire, 'P';\n@ISA = ();\n"
    )
    packages = {p.name: p.parents for p in scan_perl_packages(source)}
    assert packages == {"A": ("New",), "B": ("Y", "X", "Z"), "C": ()}


def test_a_duplicate_package_binds_nothing_even_when_one_copy_defines_the_sub(
    tmp_path: Path,
) -> None:
    sources = {
        "lib/Pkg.pm": "package Pkg;\nsub new { bless {}, shift }\n1;\n",
        "old/Pkg.pm": "package Pkg;\nsub draw { 1 }\n1;\n",
        "bin/run.pl": "sub run { Pkg->draw(); Pkg->new(); }\n",
    }
    assert _calls_from(_build(tmp_path, sources), "bin/run.pl::run") == {}


def test_packages_are_read_from_the_analysed_revision(tmp_path: Path) -> None:
    sources = {
        **_SOURCES,
        "lib/Shape/Square.pm": (
            "package Shape::Square;\nour @ISA = ('Shape::Base');\n"
            "sub area { my $self = shift; $self->outline(); }\n1;\n"
        ),
    }
    # The working tree has a Square with no parent; the revision under analysis has one.
    on_disk = {"lib/Shape/Square.pm": "package Shape::Square;\nsub area { 0 }\n1;\n"}
    graph = _build(tmp_path, sources, on_disk=on_disk)
    assert _calls_from(graph, "lib/Shape/Square.pm::Shape::Square::area") == {
        "lib/Shape/Base.pm::Shape::Base::outline": "self_inherited"
    }


def test_explicit_main_calls_bind_to_top_level_subs(tmp_path: Path) -> None:
    sources = {
        "bin/run.pl": (
            "sub helper { 1 }\n"
            "package A;\n"
            "sub go { main::helper(); }\n"
            "package main;\n"
            "sub top { main::helper(); }\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "bin/run.pl::A::go") == {"bin/run.pl::helper": "scoped_name"}
    assert _calls_from(graph, "bin/run.pl::top") == {"bin/run.pl::helper": "scoped_name"}


def test_a_bare_call_in_a_package_never_reaches_main(tmp_path: Path) -> None:
    sources = {
        "lib/Mixed.pm": (
            "sub helper { 0 }\n"
            "sub only_main { 0 }\n"
            "sub go { helper(); }\n"
            "package A;\n"
            "sub helper { 1 }\n"
            "sub run { helper(); only_main(); }\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "lib/Mixed.pm::A::run") == {
        "lib/Mixed.pm::A::helper": "enclosing_class"
    }
    assert _calls_from(graph, "lib/Mixed.pm::go") == {"lib/Mixed.pm::helper": "same_file"}


def test_a_block_package_does_not_end_the_package_around_it(tmp_path: Path) -> None:
    sources = {
        "lib/A.pm": (
            "package A;\n"
            "package B { sub inner { 1 } }\n"
            "sub helper { 2 }\n"
            "sub run { helper(); }\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "lib/A.pm::A::run") == {"lib/A.pm::A::helper": "enclosing_class"}


def test_packages_on_one_line_are_told_apart_by_position() -> None:
    source = b"package A; our @ISA = ('PA'); package B; our @ISA = ('PB'); sub run { 1 }\n"
    index = PerlPackageIndex.build([("one.pm", source)])
    assert index.package_at("one.pm", source.index(b"PA")) == "A"
    assert index.package_at("one.pm", source.index(b"sub run")) == "B"
    assert index.mro("A") == ("A", "PA")
    assert index.mro("B") == ("B", "PB")


def test_isa_read_as_a_value_is_not_a_write() -> None:
    source = (
        b"package A;\nour @ISA = ('Base');\nmy @saved = @ISA;\n"
        b"push @other, @ISA;\nprint scalar(@ISA);\n"
    )
    assert {p.name: p.parents for p in scan_perl_packages(source)} == {"A": ("Base",)}


def test_a_copied_package_keeps_its_own_parents(tmp_path: Path) -> None:
    sources = {
        "lib/PA.pm": "package PA;\nsub only_a { 1 }\n1;\n",
        "lib/PB.pm": "package PB;\nsub only_b { 2 }\n1;\n",
        "lib/Pkg.pm": (
            "package Pkg;\nuse parent -norequire, 'PA';\n"
            "sub run { my $self = shift; $self->only_a(); $self->only_b(); }\n1;\n"
        ),
        "legacy/Pkg.pm": "package Pkg;\nuse parent -norequire, 'PB';\n1;\n",
    }
    assert _calls_from(_build(tmp_path, sources), "lib/Pkg.pm::Pkg::run") == {
        "lib/PA.pm::PA::only_a": "self_inherited"
    }


def test_a_named_sub_declared_inside_another_is_package_global(tmp_path: Path) -> None:
    sources = {
        "lib/A.pm": (
            "package A;\n"
            "sub outer {\n  sub helper { 1 }\n  my sub lexical { 2 }\n}\n"
            "sub run { helper(); }\n1;\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert "lib/A.pm::A::helper" in graph
    assert "lib/A.pm::A::lexical" not in graph
    assert _calls_from(graph, "lib/A.pm::A::run") == {"lib/A.pm::A::helper": "enclosing_class"}


def test_main_inherits_through_use_parent(tmp_path: Path) -> None:
    sources = {
        "lib/Base.pm": "package Base;\nsub work { 1 }\n1;\n",
        "bin/run.pl": (
            "use parent -norequire, 'Base';\n"
            "sub go {\n  main->work();\n  __PACKAGE__->work();\n}\n"
        ),
    }
    assert _calls_from(_build(tmp_path, sources), "bin/run.pl::go") == {
        "lib/Base.pm::Base::work": "self_inherited"
    }


def test_a_qualified_sub_declaration_belongs_to_its_package(tmp_path: Path) -> None:
    sources = {
        "lib/A.pm": (
            "package A;\n"
            "sub B::helper { 1 }\n"
            "sub main::top { 2 }\n"
            "sub run { B::helper(); }\n1;\n"
        ),
    }
    graph = _build(tmp_path, sources)
    assert "lib/A.pm::B::helper" in graph
    assert "lib/A.pm::top" in graph
    assert _calls_from(graph, "lib/A.pm::A::run") == {"lib/A.pm::B::helper": "scoped_name"}


def test_a_lexical_sub_is_never_a_package_symbol(tmp_path: Path) -> None:
    sources = {
        "lib/A.pm": "package A;\n{\n  my sub hidden { 1 }\n}\nstate sub kept { 2 }\nsub run { A::hidden(); }\n1;\n",
    }
    graph = _build(tmp_path, sources)
    assert "lib/A.pm::A::hidden" not in graph
    assert "lib/A.pm::A::kept" not in graph
    assert _calls_from(graph, "lib/A.pm::A::run") == {}


def test_class_blocks_own_their_methods(tmp_path: Path) -> None:
    sources = {
        "lib/x.pm": (
            "use v5.38;\nuse feature 'class';\n"
            "class Foo {\n  method area { return $self->side; }\n  method side { 1 }\n}\n"
            "class Bar {\n  method side { 2 }\n}\n"
        ),
        "lib/y.pm": "use v5.38;\nuse feature 'class';\nclass Baz 1.0;\nmethod go { return $self->area; }\n",
    }
    graph = _build(tmp_path, sources)
    assert {"lib/x.pm::Foo::side", "lib/x.pm::Bar::side"} <= set(graph)
    assert _calls_from(graph, "lib/x.pm::Foo::area") == {"lib/x.pm::Foo::side": "self_scope"}
    assert _calls_from(graph, "lib/y.pm::Baz::go") == {}


def test_main_reaches_only_its_own_or_required_subs(tmp_path: Path) -> None:
    sources = {
        "bin/a.pl": "sub run { main::other(); __PACKAGE__->other(); }\n",
        "bin/b.pl": "sub other { 2 }\n",
        "lib/helpers.pl": "sub tool { 1 }\n1;\n",
        "bin/c.pl": 'require "lib/helpers.pl";\nsub run { main::tool(); }\n',
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "bin/a.pl::run") == {}
    assert _calls_from(graph, "bin/c.pl::run") == {"lib/helpers.pl::tool": "scoped_name"}
