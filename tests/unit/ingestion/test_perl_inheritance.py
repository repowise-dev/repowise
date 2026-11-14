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


def _build(tmp_path: Path, sources: dict[str, str] = _SOURCES):
    builder = GraphBuilder(tmp_path)
    for rel, text in sources.items():
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


def test_self_calls_walk_the_parents_depth_first(tmp_path: Path) -> None:
    calls = _calls_from(_build(tmp_path), "lib/Shape/Circle.pm::area")
    assert calls == {
        "lib/Shape/Base.pm::area": "self_inherited",
        "lib/Shape/Named.pm::label": "self_inherited",
        "lib/Shape/Base.pm::outline": "self_inherited",
        "lib/Shape/Circle.pm::radius": "self_scope",
    }


def test_own_package_and_isa_assignment(tmp_path: Path) -> None:
    graph = _build(tmp_path)
    assert _calls_from(graph, "lib/Shape/Square.pm::area") == {
        "lib/Shape/Square.pm::side": "self_scope"
    }
    assert _calls_from(graph, "lib/Shape/Base.pm::draw") == {
        "lib/Shape/Base.pm::outline": "self_scope"
    }
    assert _calls_from(graph, "lib/Shape/Circle.pm::make") == {
        "lib/Shape/Base.pm::new": "self_inherited"
    }
    assert _calls_from(graph, "lib/Shape/Circle.pm::clone") == {
        "lib/Shape/Base.pm::new": "self_inherited"
    }


def test_package_receivers_dispatch_but_sub_calls_do_not(tmp_path: Path) -> None:
    calls = _calls_from(_build(tmp_path), "t/shapes.t::test_shapes")
    # ``Shape::Circle::draw()`` names a sub Circle does not define; ``$other`` is untyped.
    assert calls == {
        "lib/Shape/Base.pm::new": "self_inherited",
        "lib/Shape/Base.pm::draw": "self_inherited",
        "lib/Shape/Circle.pm::area": "receiver_typed_global",
        "lib/Shape/Base.pm::outline": "receiver_typed_global",
    }


def test_a_package_two_files_declare_binds_nothing(tmp_path: Path) -> None:
    sources = {
        **_SOURCES,
        "legacy/Shape/Base.pm": "package Shape::Base;\nsub outline { 9 }\n1;\n",
    }
    calls = _calls_from(_build(tmp_path, sources), "lib/Shape/Circle.pm::area")
    assert "lib/Shape/Base.pm::outline" not in calls
    assert "legacy/Shape/Base.pm::outline" not in calls


def test_a_copied_package_binds_its_own_file(tmp_path: Path) -> None:
    sources = {
        **_SOURCES,
        "legacy/Shape/Square.pm": "package Shape::Square;\nsub area { $_[0]->side() }\nsub side { 4 }\n1;\n",
    }
    graph = _build(tmp_path, sources)
    assert _calls_from(graph, "legacy/Shape/Square.pm::area") == {
        "legacy/Shape/Square.pm::side": "self_scope"
    }
    assert _calls_from(graph, "lib/Shape/Square.pm::area") == {
        "lib/Shape/Square.pm::side": "self_scope"
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


def test_index_orders_parents_depth_first_and_stops_on_cycles(tmp_path: Path) -> None:
    path = tmp_path / "x.pm"
    path.write_bytes(
        b"package C; our @ISA = ('B1', 'B2');\n"
        b"package B1; our @ISA = ('A');\n"
        b"package B2; our @ISA = ('A');\n"
        b"package A; our @ISA = ('C');\n"
    )
    index = PerlPackageIndex.build([("x.pm", str(path))])
    assert index.mro("C") == ("C", "B1", "A", "B2")
    assert index.package_at("x.pm", 3) == "B2"
