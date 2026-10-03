"""Unit tests for Flutter framework edges (navigation + widget tree).

Covers the two shapes the handler emits: route-table/builder edges and
runApp entry-point edges (pre-existing), plus the widget-tree pass added
for #142: build() bodies emitting parent-to-child edges between repo widget
classes, with framework widgets (Scaffold, Column, Text) skipped.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path,
        language="dart",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for src in repo.rglob("*.dart"):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        fi = _file_info(rel, str(src.resolve()))
        out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem = Path(p).stem.lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


class TestWidgetTreeEdges:
    def test_build_method_emits_parent_child_edges(self, tmp_path: Path) -> None:
        """A build() returning a repo widget emits a parent→child edge."""
        _write(
            tmp_path,
            "lib/main.dart",
            """import 'package:flutter/material.dart';
import 'cart_page.dart';

void main() => runApp(MyApp());

class MyApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      home: CartPage(),
    );
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/cart_page.dart",
            """import 'package:flutter/material.dart';

class CartPage extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(
        children: [Text('Cart')],
      ),
    );
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        # main.dart builds CartPage → edge
        assert graph.has_edge("lib/main.dart", "lib/cart_page.dart")
        # CartPage's build returns only framework widgets → no self/other edges
        assert not graph.has_edge("lib/cart_page.dart", "lib/main.dart")
        # runApp stamps MyApp's file as entry point
        assert graph.nodes["lib/main.dart"].get("is_entry_point") is True

    def test_framework_widgets_are_skipped(self, tmp_path: Path) -> None:
        """Scaffold/Column/Text resolve to nothing in the repo class map."""
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Column(children: [Text('hi')]),
    );
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)
        # No edges at all, since every constructor is a framework widget.
        assert graph.number_of_edges() == 0

    def test_widget_tree_edge_from_non_entry_file(self, tmp_path: Path) -> None:
        """A widget file (no runApp) building a repo widget emits the edge,
        which is what the runApp window heuristic cannot produce."""
        _write(
            tmp_path,
            "lib/main.dart",
            """import 'package:flutter/material.dart';
import 'app.dart';

void main() => runApp(App());
""",
        )
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';
import 'product_card.dart';

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: ProductCard(product: 'x'),
    );
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/product_card.dart",
            """import 'package:flutter/material.dart';

class ProductCard extends StatelessWidget {
  final String product;
  const ProductCard({super.key, required this.product});

  @override
  Widget build(BuildContext context) {
    return Card(child: Text(product));
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        # app.dart builds ProductCard → edge (widget-tree pass)
        assert graph.has_edge("lib/app.dart", "lib/product_card.dart")
        # main.dart's runApp window is tiny, so no accidental edge to ProductCard
        assert not graph.has_edge("lib/main.dart", "lib/product_card.dart")

    def test_route_table_edges_still_work(self, tmp_path: Path) -> None:
        """Route tables and builders still emit edges alongside the tree."""
        _write(
            tmp_path,
            "lib/main.dart",
            """import 'package:flutter/material.dart';
import 'home_page.dart';
import 'details_page.dart';

void main() => runApp(MyApp());

class MyApp extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      routes: {
        '/': (context) => HomePage(),
        '/details': (context) => DetailsPage(),
      },
    );
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/home_page.dart",
            """import 'package:flutter/material.dart';

class HomePage extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Scaffold(body: Text('Home'));
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/details_page.dart",
            """import 'package:flutter/material.dart';

class DetailsPage extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Scaffold(body: Text('Details'));
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert graph.has_edge("lib/main.dart", "lib/home_page.dart")
        assert graph.has_edge("lib/main.dart", "lib/details_page.dart")
        assert graph.nodes["lib/main.dart"].get("is_entry_point") is True


_HEADER_BAR = """import 'package:flutter/material.dart';

class HeaderBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Text('header');
  }
}
"""


class TestConstructorForms:
    """Named-constructor, generic and arrow spellings of a widget call."""

    def _run(self, tmp_path: Path, body: str, child: str) -> nx.DiGraph:
        _write(
            tmp_path,
            "lib/app.dart",
            f"""import 'package:flutter/material.dart';
import 'child.dart';

class App extends StatelessWidget {{
  @override
  {body}
}}
""",
        )
        _write(tmp_path, "lib/child.dart", child)
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)
        return graph

    def test_named_constructor(self, tmp_path: Path) -> None:
        """Badge.small() is a call to Badge."""
        graph = self._run(
            tmp_path,
            "Widget build(BuildContext context) {\n"
            "    return Column(children: [Badge.small()]);\n"
            "  }",
            """import 'package:flutter/material.dart';

class Badge extends StatelessWidget {
  const Badge.small();

  @override
  Widget build(BuildContext context) {
    return Text('badge');
  }
}
""",
        )
        assert graph.has_edge("lib/app.dart", "lib/child.dart")

    def test_generic_type_arguments(self, tmp_path: Path) -> None:
        """TypedList<int>() is a call to TypedList."""
        graph = self._run(
            tmp_path,
            "Widget build(BuildContext context) {\n"
            "    return TypedList<int>(items: const []);\n"
            "  }",
            """import 'package:flutter/material.dart';

class TypedList<T> extends StatelessWidget {
  final List<T> items;
  const TypedList({required this.items});

  @override
  Widget build(BuildContext context) {
    return Column(children: const []);
  }
}
""",
        )
        assert graph.has_edge("lib/app.dart", "lib/child.dart")

    def test_dot_builder_constructor(self, tmp_path: Path) -> None:
        """Grid.builder() is a call to Grid, not a route builder callback."""
        graph = self._run(
            tmp_path,
            "Widget build(BuildContext context) {\n"
            "    return Grid.builder(count: 3);\n"
            "  }",
            """import 'package:flutter/material.dart';

class Grid extends StatelessWidget {
  final int count;
  const Grid.builder({required this.count});

  @override
  Widget build(BuildContext context) {
    return Column(children: const []);
  }
}
""",
        )
        assert graph.has_edge("lib/app.dart", "lib/child.dart")

    def test_arrow_form_build(self, tmp_path: Path) -> None:
        """An expression-bodied build() is read up to its semicolon."""
        graph = self._run(
            tmp_path,
            "Widget build(BuildContext context) => Column(children: [HeaderBar()]);",
            _HEADER_BAR,
        )
        assert graph.has_edge("lib/app.dart", "lib/child.dart")


class TestBodyExtraction:
    def test_long_body_keeps_its_tail(self, tmp_path: Path) -> None:
        """A build() past the old 600-char window still finds its last child."""
        filler = "\n".join(
            f"      const Text('row {i} padding padding padding padding'),"
            for i in range(20)
        )
        _write(
            tmp_path,
            "lib/app.dart",
            f"""import 'package:flutter/material.dart';
import 'header_bar.dart';

class App extends StatelessWidget {{
  @override
  Widget build(BuildContext context) {{
    return Column(
      children: [
{filler}
        HeaderBar(),
      ],
    );
  }}
}}
""",
        )
        _write(tmp_path, "lib/header_bar.dart", _HEADER_BAR)
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        body_len = len((tmp_path / "lib/app.dart").read_text())
        assert body_len > 600
        assert graph.has_edge("lib/app.dart", "lib/header_bar.dart")

    def test_helper_method_subtree_is_not_followed(self, tmp_path: Path) -> None:
        """The ceiling: a child built by a helper method is invisible.

        Only build() bodies are read, so HeaderBar reached through
        _buildHeader() yields no edge.
        """
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';
import 'header_bar.dart';

class App extends StatelessWidget {
  Widget _buildHeader() => HeaderBar();

  @override
  Widget build(BuildContext context) {
    return Column(children: [_buildHeader()]);
  }
}
""",
        )
        _write(tmp_path, "lib/header_bar.dart", _HEADER_BAR)
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert not graph.has_edge("lib/app.dart", "lib/header_bar.dart")


class TestWidgetCheck:
    def test_non_widget_repo_class_yields_no_edge(self, tmp_path: Path) -> None:
        """A repo class that is not a widget is not a child, however it reads."""
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';
import 'repository.dart';

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final repo = Repository();
    return Text(repo.title);
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/repository.dart",
            """class Repository {
  final String title = 'x';
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert not graph.has_edge("lib/app.dart", "lib/repository.dart")

    def test_private_widget_is_not_read_as_its_public_namesake(
        self, tmp_path: Path
    ) -> None:
        """_AppSearchBar() is its own class, not a call to AppSearchBar."""
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';
import 'search_bar.dart';

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return _AppSearchBar();
  }
}

class _AppSearchBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Text('private');
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/search_bar.dart",
            """import 'package:flutter/material.dart';

class AppSearchBar extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Text('public');
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert not graph.has_edge("lib/app.dart", "lib/search_bar.dart")

    def test_unimported_widget_yields_no_edge(self, tmp_path: Path) -> None:
        """A repo widget shadowing a framework one is not silently adopted.

        app.dart never imports card.dart, so its Card() is Flutter's.
        """
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Card(child: Text('hi'));
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/card.dart",
            """import 'package:flutter/material.dart';

class Card extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Text('card');
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert not graph.has_edge("lib/app.dart", "lib/card.dart")

    def test_stateful_widget_edge_belongs_to_the_state_class(
        self, tmp_path: Path
    ) -> None:
        """A StatefulWidget builds nothing; its State's build() owns the edge."""
        _write(
            tmp_path,
            "lib/counter.dart",
            """import 'package:flutter/material.dart';
import 'counter_state.dart';

class Counter extends StatefulWidget {
  @override
  State<Counter> createState() => CounterState();
}
""",
        )
        _write(
            tmp_path,
            "lib/counter_state.dart",
            """import 'package:flutter/material.dart';
import 'counter.dart';
import 'header_bar.dart';

class CounterState extends State<Counter> {
  @override
  Widget build(BuildContext context) {
    return Column(children: [HeaderBar()]);
  }
}
""",
        )
        _write(tmp_path, "lib/header_bar.dart", _HEADER_BAR)
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert graph.has_edge("lib/counter_state.dart", "lib/header_bar.dart")
        assert not graph.has_edge("lib/counter.dart", "lib/header_bar.dart")


class TestSameNameCollision:
    def _write_two_badges(self, tmp_path: Path) -> None:
        badge = """import 'package:flutter/material.dart';

class Badge extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Text('badge');
  }
}
"""
        _write(tmp_path, "lib/cart/badge.dart", badge)
        _write(tmp_path, "lib/chat/badge.dart", badge)

    def test_collision_refuses_the_edge(self, tmp_path: Path) -> None:
        """Two files declare Badge and nothing says which one, so no edge."""
        self._write_two_badges(tmp_path)
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Badge();
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert not graph.has_edge("lib/app.dart", "lib/cart/badge.dart")
        assert not graph.has_edge("lib/app.dart", "lib/chat/badge.dart")

    def test_import_settles_the_collision(self, tmp_path: Path) -> None:
        """The importing file names one of the two declarers, so it wins."""
        self._write_two_badges(tmp_path)
        _write(
            tmp_path,
            "lib/app.dart",
            """import 'package:flutter/material.dart';
import 'chat/badge.dart';

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Badge();
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert graph.has_edge("lib/app.dart", "lib/chat/badge.dart")
        assert not graph.has_edge("lib/app.dart", "lib/cart/badge.dart")

    def test_part_of_file_cannot_settle_the_collision(self, tmp_path: Path) -> None:
        """A part file's imports live on the library, so the name stays open.

        Its only import is the ``part of`` pointing at the library, and the
        library declares one of the two Badges, so resolving to it would be a
        guess about which Badge the part meant.
        """
        _write(
            tmp_path,
            "lib/cart/badge.dart",
            """import 'package:flutter/material.dart';

class Badge extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Text('cart');
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/app.dart",
            """part of shell;

class App extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Badge();
  }
}
""",
        )
        _write(
            tmp_path,
            "lib/shell.dart",
            """library shell;

import 'package:flutter/material.dart';
import 'cart/badge.dart';

part 'app.dart';

class Badge extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    return Text('shell');
  }
}
""",
        )
        parsed = _build_parsed(tmp_path)
        ctx = _ctx(tmp_path, parsed)
        graph = nx.DiGraph()
        add_framework_edges(graph, parsed, ctx)

        assert not graph.has_edge("lib/app.dart", "lib/shell.dart")
        assert not graph.has_edge("lib/app.dart", "lib/cart/badge.dart")
