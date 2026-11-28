"""What the front page and the onboarding pages are written from.

Documentation in cited sections, what a user types (each with its own
declaration span), the walkthroughs already written, and the top-level parts
with their own descriptions. All read from the run, none from a model.
"""

from __future__ import annotations

from types import SimpleNamespace

import networkx as nx

from repowise.core.generation.flow_entries import program_name
from repowise.core.generation.onboarding.docs import (
    doc_sections,
    doc_sources,
    rank_by_overlap,
    rank_sections,
    readme_intro,
)
from repowise.core.generation.onboarding.surface import journey, surfaces
from repowise.core.generation.page_generator.front_page import (
    clean_summary,
    part_edges,
    top_parts,
)

README = b"""[![badge](a.svg)](https://x)
# Demo

Demo indexes a repository so engineers can ask it questions.

## Install

```bash
# clone the repo
pip install demo
```

## Usage

Run `demo init`, then `demo serve`.
"""

QUICKSTART_RST = b"""Quickstart
==========

Run ``demo init`` in a repository.

Serving
-------

``demo serve`` opens the dashboard.
"""


def _docs() -> dict[str, bytes]:
    return {
        "README.md": README,
        "docs/start/quickstart.rst": QUICKSTART_RST,
        "docs/CHANGELOG.md": b"# Changes\n\ndemo init changed.\n",
        "CONTRIBUTING.md": b"# Contributing\n\nRun the tests before a pull request.\n",
        "src/acme/notes.md": b"# Not documentation\n",
    }


class TestDocSections:
    def test_markdown_and_rst_split_by_heading_with_spans(self):
        sections = doc_sections(_docs())
        heads = {(s.path, s.heading): (s.start, s.end) for s in sections}
        assert heads[("README.md", "Install")] == (6, 12)
        assert heads[("docs/start/quickstart.rst", "Serving")] == (6, 9)

    def test_a_comment_in_a_code_fence_is_not_a_heading(self):
        assert not any(s.heading == "clone the repo" for s in doc_sections(_docs()))

    def test_changelogs_and_code_trees_are_not_documentation(self):
        paths = {s.path for s in doc_sections(_docs())}
        assert "docs/CHANGELOG.md" not in paths and "src/acme/notes.md" not in paths

    def test_rst_files_are_read_from_disk(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "guide.rst").write_bytes(QUICKSTART_RST)
        docs = doc_sources(tmp_path, {"README.md": README})
        assert set(docs) == {"README.md", "docs/guide.rst"}


class TestReadmeIntro:
    def test_each_section_keeps_its_own_span_and_loses_badges(self):
        intro = readme_intro(doc_sections(_docs()))
        assert [s.cite for s in intro] == ["README.md:2-5", "README.md:6-12", "README.md:13-15"]
        assert "badge" not in intro[0].text

    def test_capped_in_document_order(self):
        intro = readme_intro(doc_sections(_docs()), char_cap=60)
        assert intro[0].heading == "Demo" and len(intro) == 1
        assert intro[0].text.endswith("[...]")


class TestRanking:
    def test_user_documents_lead(self):
        picked = rank_sections(doc_sections(_docs()), ["demo init"], char_cap=4000)
        assert picked[0].path in ("README.md", "docs/start/quickstart.rst")

    def test_overlap_needs_a_strong_term(self):
        sections = doc_sections(_docs())
        assert rank_by_overlap(sections, "tests pull request", strong={"nothing_here"}) == []
        hits = rank_by_overlap(sections, "dashboard serving", strong={"serve"})
        assert hits and all("serve" in s.text for s in hits)


def _graph(*symbols: dict, files: tuple[str, ...] = ()) -> nx.DiGraph:
    g = nx.DiGraph()
    for path in files:
        g.add_node(path, node_type="file")
    for s in symbols:
        g.add_node(s["file_path"], node_type="file")
        g.add_node(
            f"{s['file_path']}::{s['name']}",
            node_type="symbol",
            kind="function",
            start_line=s.get("line", 1),
            decorators=s.get("decorators", ()),
            docstring=s.get("doc", ""),
            **{k: s[k] for k in ("name", "file_path")},
        )
    return g


def _project(tmp_path, script: str = "acme"):
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "{script}"\n\n[project.scripts]\n{script} = "acme.cli:main"\n'
    )
    return tmp_path


class TestSurface:
    def test_commands_carry_their_own_declaration_and_program(self, tmp_path):
        g = _graph(
            {"name": "init_command", "file_path": "src/acme/cli.py", "line": 3,
             "decorators": ['@click.command("init")'], "doc": "Index a repo.\nMore."},
            {"name": "get_answer", "file_path": "src/acme/tools.py", "decorators": ["@mcp.tool()"]},
            {"name": "cli", "file_path": "src/acme/cli.py", "decorators": ["@click.group()"]},
        )
        out = surfaces(g, lambda _p: [], _project(tmp_path), [])
        assert out.programs == ["acme"]
        assert [(c.name, c.doc, c.cite) for c in out.commands] == [
            ("acme init", "Index a repo.", "src/acme/cli.py:3")
        ]
        assert [t.name for t in out.tools] == ["get_answer"]

    def test_argparse_and_commander_commands_cite_their_call(self, tmp_path):
        src = {
            "src/acme/main.py": ["parser = x()", 'sub.add_parser("serve", help="Serve.")'],
            "web/bin/cli.ts": ["program", "  .command('build')"],
        }
        g = _graph(files=tuple(src))
        out = surfaces(g, lambda p: src.get(p, []), _project(tmp_path), [])
        assert {(c.name, c.cite) for c in out.commands} == {
            ("acme serve", "src/acme/main.py:2"),
            ("acme build", "web/bin/cli.ts:2"),
        }

    def test_public_api_keeps_names_the_docs_use(self, tmp_path):
        init = ["from .app import (", "    Demo,  # the app", "    helper,", ")"]
        g = _graph(
            {"name": "Demo", "file_path": "src/acme/app.py"},
            {"name": "helper", "file_path": "src/acme/app.py"},
            files=("src/acme/__init__.py",),
        )
        docs = doc_sections({"README.md": b"# Acme\n\nCall `Demo()` to start.\n"})
        read = {"src/acme/__init__.py": init}.get
        out = surfaces(g, lambda p: read(p) or [], tmp_path, docs)
        assert [a.name for a in out.api] == ["Demo"]

    def test_program_names_are_names_only(self, tmp_path):
        (tmp_path / "package.json").write_text('{"bin": {"## ignore all": "y.js"}}')
        assert program_name(tmp_path, nx.DiGraph()) == ""
        (tmp_path / "package.json").unlink()
        (tmp_path / "go.mod").write_text("module github.com/acme/site/v2\n")
        g = nx.DiGraph()
        g.add_node("main.go")
        assert program_name(tmp_path, g) == "site"


class TestJourney:
    def test_opening_and_at_a_glance(self):
        page = (
            "# How demo init works\n\n`demo init` builds the index.\n\n"
            "```mermaid\nflowchart LR\n```\n\n## At a glance\n\n1. Parse.\n2. Store.\n\n## Parsing\n\nText.\n"
        )
        j = journey("fallback", page)
        assert j.title == "How demo init works"
        assert j.opening == "`demo init` builds the index."
        assert j.stages == "1. Parse.\n2. Store."

    def test_stages_fall_back_to_section_names(self):
        page = "# T\n\nOpens.\n\n## Reading files\n\nx\n\n## Where to read next\n\ny\n"
        assert journey("T", page).stages == "- Reading files"


def _group(key: str, display: str, files: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(key=key, display=display, file_paths=files, context_paths=())


class TestParts:
    def test_a_lone_top_part_is_opened_and_described_by_its_own_code(self):
        groups = [
            _group("src/acme", "Demo Overview", ("src/acme/__init__.py", "src/acme/app.py")),
            _group("src/acme/json", "Json", ("src/acme/json/__init__.py",)),
        ]
        source = {
            "src/acme/__init__.py": b'"""The demo application."""\n',
            "src/acme/json/__init__.py": b'"""JSON helpers."""\n',
        }
        summaries = {"src/acme": "`src/acme` **Language:** python | **Files:** 2"}
        parts = top_parts(groups, summaries, source)
        assert [(p.path, p.summary) for p in parts] == [
            ("src/acme", "The demo application."),
            ("src/acme/json", "JSON helpers."),
        ]

    def test_edges_between_parts_with_names(self):
        groups = [_group("a", "A", ("a/x.py",)), _group("b", "B", ("b/y.py",))]
        parts = top_parts(groups, {"a": "Part A does work.", "b": "Part B."}, {})
        graph = nx.DiGraph()
        graph.add_edge("a/x.py", "b/y.py", edge_type="imports", imported_names=["Thing"])
        graph.add_edge("b/y.py", "a/x.py", edge_type="calls")
        edges = part_edges(graph, groups, parts)
        assert [(e.src, e.dst, e.names) for e in edges] == [("a", "b", ("Thing",))]

    def test_summary_is_one_plain_line(self):
        text = "# Title\n\nThe [engine](x.md) parses files. It stores them.\n\n```py\ncode\n```"
        assert clean_summary(text) == "The engine parses files. It stores them."
