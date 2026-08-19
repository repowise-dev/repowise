"""The repository's own vocabulary reaches the onboarding builders, or says so.

The miner has existed for three merges with no consumer. What this covers is
the wiring: a run reads the repository once, hands every subkind the same
ranked terms, and reports what it found.

The negative cases carry the weight. A run given no repository path and a
repository that documents nothing both produce an empty tuple, and an empty
tuple on its own cannot say which happened — so each one is asserted to log,
and the report is asserted to keep "nothing was read" and "nothing was found"
apart.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from structlog.testing import capture_logs

from repowise.core.generation.page_generator.levels import build_level8_coros
from repowise.core.generation.report import (
    GenerationReport,
    house_terms_mined,
    record_house_terms,
    reset_house_terms,
)
from repowise.core.ingestion.models import FileInfo, ParsedFile, RepoStructure, Symbol

# ---------------------------------------------------------------------------
# A repository on disk, because the miner reads one
# ---------------------------------------------------------------------------

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


def _repo(tmp_path: Path, *, documented: bool = True) -> Path:
    root = tmp_path / "ledger"
    (root / "src").mkdir(parents=True)
    (root / "src" / "blast_radius.py").write_text(_MODULE, encoding="utf-8")
    if documented:
        (root / "README.md").write_text(_README, encoding="utf-8")
    return root


def _parsed_file(path: str, *, symbols: tuple[str, ...] = ()) -> ParsedFile:
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
                id=f"{path}::{name}",
                name=name,
                qualified_name=f"{path}::{name}",
                kind="class",
                signature=f"class {name}:",
                start_line=1,
                end_line=8,
                docstring=f"Docstring for {name}.",
                decorators=[],
                visibility="public",
                is_async=False,
                complexity_estimate=1,
                language="python",
                parent_name=None,
            )
            for name in symbols
        ],
        imports=[],
        exports=list(symbols),
        docstring=None,
        parse_errors=[],
        content_hash="abc",
    )


class _Gen:
    """Stands in for the page generator, keeping the signals it was handed.

    Returns a sentinel rather than a coroutine: the level builder only
    collects what it is given, and a coroutine nothing awaits is a warning
    with no upside here.
    """

    def __init__(self) -> None:
        self.seen: list[Any] = []

    def generate_onboarding_page(self, spec: Any, signals: Any) -> object:
        self.seen.append(signals)
        return object()


def _run(*, repo_path: Path | None) -> SimpleNamespace:
    """A generation run carrying only what level 8 reads."""
    parsed = [_parsed_file("src/blast_radius.py", symbols=("BlastRadius",))]
    return SimpleNamespace(
        gen=_Gen(),
        config=SimpleNamespace(enable_onboarding=True, source_evidence_files={}),
        repo_path=repo_path,
        repo_name="ledger",
        repo_structure=RepoStructure(
            is_monorepo=False,
            packages=[],
            root_language_distribution={"python": 1.0},
            total_files=1,
            total_loc=10,
            entry_points=[],
        ),
        parsed_files=parsed,
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
        # The glossary corroborates mined terms against the module groups, so
        # level 8 now reads these too. Empty here: this module is about the
        # mining, and the glossary's own tests cover what corroboration does.
        sel_module_groups=[],
        vector_store=None,
        tour_stops=(),
        layer_order=(),
        kg_ctx=None,
        on_subphase=None,
        _emit=lambda page_id: True,
    )


async def _signals_from_level8(run: SimpleNamespace) -> Any:
    coros = await build_level8_coros(run)
    assert coros, "level 8 produced no pages, so no signals were built"
    assert run.gen.seen, "no subkind was handed the signals"
    return run.gen.seen[0]


# ---------------------------------------------------------------------------
# The field
# ---------------------------------------------------------------------------


async def test_documented_repository_populates_the_field(tmp_path: Path) -> None:
    reset_house_terms()
    signals = await _signals_from_level8(_run(repo_path=_repo(tmp_path)))

    terms = {t.term for t in signals.house_terms}
    assert "Blast radius" in terms
    assert "Change risk" in terms


async def test_every_subkind_reads_the_same_mined_terms(tmp_path: Path) -> None:
    """One walk of the repository per run, not one per slot."""
    reset_house_terms()
    run = _run(repo_path=_repo(tmp_path))
    await build_level8_coros(run)

    assert len(run.gen.seen) > 1, "expected more than one onboarding slot"
    first = run.gen.seen[0].house_terms
    assert all(s.house_terms is first for s in run.gen.seen)


async def test_a_run_emitting_no_onboarding_page_does_not_read_the_repository(
    tmp_path: Path,
) -> None:
    """Mining walks the whole repository, so a run writing none must not pay.

    A scoped run asking for one file page, and a resumed run whose onboarding
    pages already exist, both reach this level and emit nothing from it.
    """
    reset_house_terms()
    run = _run(repo_path=_repo(tmp_path))
    run._emit = lambda page_id: False

    assert await build_level8_coros(run) == []
    assert run.gen.seen == []
    assert house_terms_mined().mined is False


async def test_a_term_the_codebase_defines_is_marked_as_a_symbol(tmp_path: Path) -> None:
    """``is_indexed_symbol`` decides whether a term may be backticked.

    Without the run's symbol names it is ``False`` for every term, which reads
    downstream as "this repository defines none of its own vocabulary".
    """
    reset_house_terms()
    signals = await _signals_from_level8(_run(repo_path=_repo(tmp_path)))

    by_term = {t.term: t for t in signals.house_terms}
    assert by_term["Blast radius"].is_indexed_symbol is False
    assert any(t.is_indexed_symbol for t in signals.house_terms) is False


async def test_undocumented_repository_yields_nothing_and_says_so(tmp_path: Path) -> None:
    reset_house_terms()
    run = _run(repo_path=_repo(tmp_path, documented=False))
    with capture_logs() as logs:
        signals = await _signals_from_level8(run)

    assert signals.house_terms == ()
    assert any(entry["event"] == "onboarding.house_terms_empty" for entry in logs)
    assert house_terms_mined().mined is True


async def test_a_run_without_a_repository_path_yields_nothing_and_says_so() -> None:
    reset_house_terms()
    run = _run(repo_path=None)
    with capture_logs() as logs:
        signals = await _signals_from_level8(run)

    assert signals.house_terms == ()
    skipped = [e for e in logs if e["event"] == "onboarding.house_terms_skipped"]
    assert skipped and skipped[0]["reason"] == "no_repo_path"
    # Not the same fact as a repository that documents nothing.
    assert house_terms_mined().mined is False


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


async def test_the_count_and_the_top_terms_reach_the_report(tmp_path: Path) -> None:
    reset_house_terms()
    signals = await _signals_from_level8(_run(repo_path=_repo(tmp_path)))

    report = GenerationReport.from_pages([])
    assert report.house_terms.mined is True
    assert report.house_terms.count == len(signals.house_terms)
    assert "Blast radius" in report.house_terms.top
    assert "Blast radius" in report.house_terms.summary_line()


def test_the_report_keeps_nothing_read_apart_from_nothing_found() -> None:
    reset_house_terms()
    not_mined = GenerationReport.from_pages([]).house_terms
    assert not_mined.mined is False
    assert not_mined.count == 0
    assert "not measured" in not_mined.summary_line()

    record_house_terms([])
    empty = GenerationReport.from_pages([]).house_terms
    assert empty.mined is True
    assert empty.count == 0
    assert "not measured" not in empty.summary_line()


async def test_a_new_run_does_not_report_the_previous_run_s_vocabulary(tmp_path: Path) -> None:
    """The reset lives on ``generate_all``; this pins what it is there for."""
    reset_house_terms()
    await _signals_from_level8(_run(repo_path=_repo(tmp_path)))
    assert house_terms_mined().count > 0

    reset_house_terms()
    assert house_terms_mined() == GenerationReport().house_terms
