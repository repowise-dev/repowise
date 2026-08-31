"""Unit tests for Flutter framework edges (navigation + widget tree).

Covers the two shapes the handler emits: route-table/builder edges and
runApp entry-point edges (pre-existing), plus the widget-tree pass added
for #142 — build() bodies emitting parent→child edges between repo widget
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
        # No edges at all — every constructor is a framework widget.
        assert graph.number_of_edges() == 0

    def test_widget_tree_edge_from_non_entry_file(self, tmp_path: Path) -> None:
        """A widget file (no runApp) building a repo widget emits the edge —
        this is the pass the runApp window heuristic cannot produce."""
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
        # main.dart's runApp window is tiny — no accidental edge to ProductCard
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
