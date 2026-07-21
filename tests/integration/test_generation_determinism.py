"""Two indexes of the same tree must produce the same page ids.

The graph's node and edge insertion order is not stable between runs: files
are parsed in a process pool and co-change edges arrive in git-indexer thread
completion order. Anything that ranks on a score without a tiebreak, or turns
a position in an unsorted list into an id, inherits that instability — and an
id that moves means the update path deletes and recreates the page instead of
updating it, losing its history.

Running the pipeline twice in one process would not catch this: the hash seed
is fixed for the life of the process, so both runs would see the same order.
These tests feed the graph the same files in two different orders instead,
which is what actually varies in production.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.models import GenerationConfig, scc_page_slug
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import PackageInfo, ParsedFile, RepoStructure
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.providers.llm.template import TemplateProvider

SAMPLE_REPO = Path(__file__).parents[1] / "fixtures" / "sample_repo"


def _parse_sample_repo() -> tuple[list[ParsedFile], dict[str, bytes]]:
    traverser = FileTraverser(SAMPLE_REPO)
    parser = ASTParser()
    parsed_files: list[ParsedFile] = []
    source_map: dict[str, bytes] = {}
    for fi in traverser.traverse():
        try:
            src = Path(fi.abs_path).read_bytes()
            parsed_files.append(parser.parse_file(fi, src))
            source_map[fi.path] = src
        except Exception:
            pass
    return parsed_files, source_map


async def _generate(parsed_files, source_map, tmp, *, order) -> list:
    """Generate the wiki with files added to the graph in *order*."""
    builder = GraphBuilder()
    for p in order:
        builder.add_file(p)
    builder.build()

    packages = [
        PackageInfo(
            name=d.name, path=d.name, language="unknown", entry_points=[], manifest_file=""
        )
        for d in SAMPLE_REPO.iterdir()
        if d.is_dir()
    ]
    repo_structure = RepoStructure(
        is_monorepo=len(packages) > 1,
        packages=packages,
        root_language_distribution={"python": 0.7, "typescript": 0.3},
        total_files=len(parsed_files),
        total_loc=sum(
            len(source_map.get(p.file_info.path, b"").splitlines()) for p in parsed_files
        ),
        entry_points=[],
    )
    config = GenerationConfig(deterministic=True, max_concurrency=3, jobs_dir=str(tmp / "jobs"))
    generator = PageGenerator(TemplateProvider(), ContextAssembler(config), config)
    return await generator.generate_all(
        parsed_files, source_map, builder, repo_structure, "sample_repo", job_system=None
    )


@pytest.fixture(scope="module")
async def two_runs(tmp_path_factory):
    """The same repo generated twice, with opposite graph insertion orders."""
    tmp = tmp_path_factory.mktemp("gen_determinism")
    parsed_files, source_map = _parse_sample_repo()
    forward = await _generate(parsed_files, source_map, tmp / "a", order=parsed_files)
    reverse = await _generate(
        parsed_files, source_map, tmp / "b", order=list(reversed(parsed_files))
    )
    return forward, reverse


class TestPageIdStability:
    def test_runs_are_not_empty(self, two_runs):
        forward, reverse = two_runs
        assert forward and reverse

    def test_page_id_sets_are_identical(self, two_runs):
        """The headline contract: same tree, same commit, same page ids."""
        forward, reverse = two_runs
        assert {p.page_id for p in forward} == {p.page_id for p in reverse}

    def test_target_paths_are_identical_per_page_type(self, two_runs):
        """A weaker restatement that localises a failure to one page type."""
        forward, reverse = two_runs

        def by_type(pages):
            out: dict[str, set[str]] = {}
            for p in pages:
                out.setdefault(p.page_type, set()).add(p.target_path)
            return out

        assert by_type(forward) == by_type(reverse)

    def test_titles_are_identical(self, two_runs):
        """Titles carry the derived ids (community numbers, cycle slugs), so a
        stable id set with unstable titles still means a churning wiki."""
        forward, reverse = two_runs
        f = {p.page_id: p.title for p in forward}
        r = {p.page_id: p.title for p in reverse}
        assert f == r


class TestSccSlug:
    """SCC pages are the case that motivated the content-derived id: the
    sample repo is a DAG and has none, so exercise the slug directly."""

    def test_slug_ignores_member_order(self):
        assert scc_page_slug(["b.py", "a.py"]) == scc_page_slug(["a.py", "b.py"])

    def test_slug_distinguishes_different_cycles(self):
        assert scc_page_slug(["a.py", "b.py"]) != scc_page_slug(["a.py", "c.py"])

    def test_slug_is_page_id_shaped(self):
        slug = scc_page_slug(["a.py", "b.py"])
        assert slug.startswith("scc-")
        # No path separator and no extension: the MCP answer layer uses that
        # shape to keep graph ids out of agent-readable file targets.
        assert "/" not in slug and "." not in slug

    def test_component_order_survives_graph_insertion_order(self):
        """GraphBuilder-level check that the component list itself is stable."""
        from repowise.core.ingestion.graph._metrics import MetricsMixin

        cycles = [("a.py", "b.py"), ("c.py", "d.py"), ("e.py", "f.py")]

        def components(edge_order):
            g = nx.DiGraph()
            for u, v in edge_order:
                g.add_edge(u, v)
                g.add_edge(v, u)
            holder = type("H", (MetricsMixin,), {})()
            holder.graph = lambda: g
            holder.file_subgraph = lambda: g
            return [sorted(c) for c in holder.strongly_connected_components()]

        assert components(cycles) == components(list(reversed(cycles)))
