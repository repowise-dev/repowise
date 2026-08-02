"""The capability table reaches the overview, and is mined once per run.

The helpers can be right while the page has no table -- that already happened
once with the package table -- so this goes through ``build_level6_coros`` and
asserts what the generator was actually handed.

The two things worth pinning are the corroboration source and the cost. Module
groups are cut before any level runs, so a scoped run corroborates against the
same names a full one does; and the miner walks the whole repository, so
levels 6 and 8 must share one pass rather than paying for two.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from structlog.testing import capture_logs

from repowise.core.generation.page_generator.levels import (
    build_level6_coros,
    build_level8_coros,
)
from repowise.core.generation.report import reset_house_terms
from repowise.core.ingestion.models import FileInfo, ParsedFile, RepoStructure, Symbol

_README = """\
# Ledger

## Blast radius

Blast radius is the set of files a change can reach through the import graph.

## Change risk

Change risk scores a diff against the history of the files it touches.
"""

_MODULE = '''\
"""Compute the blast radius of a change, and its change risk.

Blast radius is measured over the resolved import graph.
"""


class BlastRadius:
    """One change's blast radius."""
'''

#: What the structural side independently arrived at. "Blast radius" is named
#: by both; "Change risk" is in the documents only.
MODULE_GROUPS = [SimpleNamespace(display="Blast Radius Evaluation", label="", key="src")]


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "ledger"
    (root / "src").mkdir(parents=True)
    (root / "src" / "blast_radius.py").write_text(_MODULE, encoding="utf-8")
    (root / "README.md").write_text(_README, encoding="utf-8")
    return root


def _parsed_file(path: str) -> ParsedFile:
    info = FileInfo(
        path=path,
        abs_path=f"/repo/{path}",
        language="python",
        size_bytes=256,
        git_hash="abc",
        last_modified=datetime(2026, 1, 1, tzinfo=UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
    return ParsedFile(
        file_info=info,
        symbols=[
            Symbol(
                id=f"{path}::BlastRadius",
                name="BlastRadius",
                qualified_name=f"{path}::BlastRadius",
                kind="class",
                signature="class BlastRadius:",
                start_line=1,
                end_line=8,
                docstring="One change's blast radius.",
                decorators=[],
                visibility="public",
                is_async=False,
                complexity_estimate=1,
                language="python",
                parent_name=None,
            )
        ],
        imports=[],
        exports=["BlastRadius"],
        docstring=None,
        parse_errors=[],
        content_hash="abc",
    )


class _Gen:
    """Keeps what the level builder handed it, and returns no coroutine."""

    def __init__(self) -> None:
        self.overview_kwargs: dict[str, Any] = {}
        self.onboarding_signals: list[Any] = []

    def generate_repo_overview(self, *args: Any, **kwargs: Any) -> object:
        self.overview_kwargs = kwargs
        return object()

    def generate_onboarding_page(self, spec: Any, signals: Any) -> object:
        self.onboarding_signals.append(signals)
        return object()


def _run(tmp_path: Path, *, module_groups: list[Any] | None = None) -> SimpleNamespace:
    reset_house_terms()
    return SimpleNamespace(
        gen=_Gen(),
        config=SimpleNamespace(enable_onboarding=True, source_evidence_files={}),
        repo_path=_repo(tmp_path),
        repo_name="ledger",
        repo_structure=RepoStructure(
            is_monorepo=False,
            packages=[],
            root_language_distribution={"python": 1.0},
            total_files=1,
            total_loc=10,
            entry_points=[],
        ),
        parsed_files=[_parsed_file("src/blast_radius.py")],
        source_map={},
        graph_builder=SimpleNamespace(
            community_info=lambda: {},
            execution_flows=lambda: SimpleNamespace(flows=[]),
        ),
        pagerank={},
        betweenness={},
        community={},
        sccs=(),
        git_meta_map=None,
        dead_code_by_file={},
        decisions_all=(),
        external_systems=(),
        completed_page_summaries={},
        sel_module_groups=MODULE_GROUPS if module_groups is None else module_groups,
        tour_stops=(),
        layer_order=(),
        kg_ctx=None,
        on_subphase=None,
        _emit=lambda page_id: True,
    )


def test_the_overview_is_handed_the_corroborated_terms(tmp_path):
    run = _run(tmp_path)
    assert build_level6_coros(run), "level 6 produced no overview"

    picked = [c.term for c in run.gen.overview_kwargs["capabilities"]]
    assert picked == ["Blast radius"]


def test_a_term_no_module_page_names_does_not_reach_the_overview(tmp_path):
    """ "Change risk" is in the README and in a docstring, so it is a mined
    term. No module page mentions it, so the front page does not claim it."""
    run = _run(tmp_path)
    build_level6_coros(run)

    picked = [c.term for c in run.gen.overview_kwargs["capabilities"]]
    assert "Change risk" not in picked


def test_a_community_label_does_not_corroborate(tmp_path):
    """Titles only, and this is why.

    A community label is a broad phrase covering a whole layer, so it
    corroborates almost any common word that appears in it. Including labels
    put "Architecture" and "Workspace" on the front page of a real render. A
    group *title* names one part of the system, which is the claim the
    corroboration is supposed to be making.
    """
    run = _run(
        tmp_path,
        module_groups=[
            SimpleNamespace(
                display="Ledger Postings",
                label="Change Risk and Ingestion Engine",
                key="src/change_risk",
            )
        ],
    )
    build_level6_coros(run)

    assert [c.term for c in run.gen.overview_kwargs["capabilities"]] == []


def test_no_module_groups_means_no_table_and_a_log(tmp_path):
    """A repository the grouper produced nothing for has nothing to
    corroborate against. The section going missing is correct, not silent."""
    run = _run(tmp_path, module_groups=[])
    with capture_logs() as logs:
        build_level6_coros(run)

    assert run.gen.overview_kwargs["capabilities"] == []
    assert any(e["event"] == "generation.overview_capability_table_absent" for e in logs)


def test_the_repository_is_mined_once_for_both_levels(tmp_path):
    """Mining walks the whole repository. Two consumers, one walk."""
    run = _run(tmp_path)
    build_level6_coros(run)
    build_level8_coros(run)

    from_overview = tuple(c.term for c in run.gen.overview_kwargs["capabilities"])
    onboarding_terms = run.gen.onboarding_signals[0].house_terms
    assert from_overview
    assert onboarding_terms
    # Same objects, so the same pass produced both.
    assert run._house_terms is onboarding_terms
    assert all(
        t.term in {h.term for h in onboarding_terms}
        for t in run.gen.overview_kwargs["capabilities"]
    )


def test_onboarding_still_gets_its_terms_when_the_overview_mined_first(tmp_path):
    """Order independence: level 6 now runs the miner, and level 8 read it
    from a warm cache rather than from a fresh walk."""
    run = _run(tmp_path)
    build_level6_coros(run)
    build_level8_coros(run)

    assert [t.term for t in run.gen.onboarding_signals[0].house_terms] == [
        "Blast radius",
        "Change risk",
    ]
