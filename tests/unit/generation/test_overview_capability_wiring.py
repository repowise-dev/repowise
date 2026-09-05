"""The capability table reaches the overview, and is mined once per run.

The helpers can be right while the page has no table -- that already happened
once with the package table -- so this goes through ``build_level6_coros`` and
asserts what the generator was actually handed.

The two things worth pinning are the corroboration corpus and the cost. Each
module group contributes its title and its summary -- from this run when
level 4 wrote the page, from the store when it did not, so a scoped run
corroborates against as much as a full one and the front-page section does not
vanish from a `repowise update`. And the miner walks the whole repository, so
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


class _Store:
    """A vector store holding one module summary from a previous run."""

    def __init__(self, summaries: dict[str, str] | None = None, fail: bool = False) -> None:
        self.summaries = summaries or {}
        self.fail = fail
        self.asked: list[str] = []

    async def get_page_summaries_by_paths(self, paths: list[str]) -> dict[str, dict]:
        self.asked.extend(paths)
        if self.fail:
            raise RuntimeError("store unavailable")
        return {p: {"summary": s} for p, s in self.summaries.items() if p in paths}


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


def _run(
    tmp_path: Path,
    *,
    module_groups: list[Any] | None = None,
    written: dict[str, str] | None = None,
    store: Any = None,
) -> SimpleNamespace:
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
        completed_page_summaries=dict(written or {}),
        sel_module_groups=MODULE_GROUPS if module_groups is None else module_groups,
        vector_store=store,
        tour_stops=(),
        layer_order=(),
        kg_ctx=None,
        on_subphase=None,
        _emit=lambda page_id: True,
    )


async def test_the_overview_is_handed_the_corroborated_terms(tmp_path):
    run = _run(tmp_path)
    assert await build_level6_coros(run), "level 6 produced no overview"

    picked = [c.term for c in run.gen.overview_kwargs["capabilities"]]
    assert picked == ["Blast radius"]


async def test_a_term_no_module_page_names_does_not_reach_the_overview(tmp_path):
    """ "Change risk" is in the README and in a docstring, so it is a mined
    term. No module page mentions it, so the front page does not claim it."""
    run = _run(tmp_path)
    await build_level6_coros(run)

    picked = [c.term for c in run.gen.overview_kwargs["capabilities"]]
    assert "Change risk" not in picked


async def test_a_community_label_does_not_corroborate(tmp_path):
    """The corpus is a group's title and summary. Its label is not in it.

    A community label is a broad phrase covering a whole layer, so it
    corroborates almost any common word that appears in it. Including labels
    put "Architecture" and "Workspace" on the front page of a real render. A
    title and a summary describe one part of the system, which is the claim
    the corroboration is supposed to be making.
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
    await build_level6_coros(run)

    assert [c.term for c in run.gen.overview_kwargs["capabilities"]] == []


async def test_a_summary_written_this_run_corroborates(tmp_path):
    """Titles are ~90 short strings and too thin a net on their own. The
    summaries are what make the corroboration mean something."""
    run = _run(
        tmp_path, written={"src": "Scores a diff by the change risk of the files it touches."}
    )
    await build_level6_coros(run)

    assert "Change risk" in [c.term for c in run.gen.overview_kwargs["capabilities"]]


async def test_a_summary_from_the_store_corroborates_when_this_run_wrote_none(tmp_path):
    """The case that made the first version wrong.

    A scoped run -- every `repowise update` -- writes no module pages, so the
    corpus was empty and the front-page section disappeared. The summaries
    are still in the store from the last full run.
    """
    store = _Store({"src": "Scores a diff by the change risk of the files it touches."})
    run = _run(tmp_path, store=store)
    await build_level6_coros(run)

    assert store.asked == ["src"]
    assert "Change risk" in [c.term for c in run.gen.overview_kwargs["capabilities"]]


async def test_this_run_wins_over_the_store(tmp_path):
    """A page written a moment ago is fresher than the one in the store, and
    asking for it again would be a round-trip for a worse answer."""
    store = _Store({"src": "stale text naming nothing"})
    run = _run(
        tmp_path,
        written={"src": "Scores a diff by the change risk of the files it touches."},
        store=store,
    )
    await build_level6_coros(run)

    assert store.asked == []
    assert "Change risk" in [c.term for c in run.gen.overview_kwargs["capabilities"]]


async def test_a_store_that_cannot_answer_costs_reach_not_the_page(tmp_path):
    """Corroboration is best-effort. A store failure must not lose the
    overview, and must not pass silently either."""
    run = _run(tmp_path, store=_Store(fail=True))
    with capture_logs() as logs:
        assert await build_level6_coros(run), "the overview was lost to a store error"

    assert any(e["event"] == "generation.overview_corroboration_store_read_failed" for e in logs)
    # The titles still corroborate on their own.
    assert [c.term for c in run.gen.overview_kwargs["capabilities"]] == ["Blast radius"]


async def test_no_module_groups_means_no_table_and_a_log(tmp_path):
    """A repository the grouper produced nothing for has nothing to
    corroborate against. The section going missing is correct, not silent."""
    run = _run(tmp_path, module_groups=[])
    with capture_logs() as logs:
        await build_level6_coros(run)

    assert run.gen.overview_kwargs["capabilities"] == []
    assert any(e["event"] == "generation.overview_capability_table_absent" for e in logs)


async def test_the_repository_is_mined_once_for_both_levels(tmp_path):
    """Mining walks the whole repository. Two consumers, one walk."""
    run = _run(tmp_path)
    await build_level6_coros(run)
    await build_level8_coros(run)

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


async def test_onboarding_still_gets_its_terms_when_the_overview_mined_first(tmp_path):
    """Order independence: level 6 now runs the miner, and level 8 read it
    from a warm cache rather than from a fresh walk."""
    run = _run(tmp_path)
    await build_level6_coros(run)
    await build_level8_coros(run)

    assert [t.term for t in run.gen.onboarding_signals[0].house_terms] == [
        "Blast radius",
        "Change risk",
    ]


# ---------------------------------------------------------------------------
# The glossary reads the same corroboration corpus
# ---------------------------------------------------------------------------
#
# It is the second consumer of both mined vocabulary and module corroboration.
# Both cost a pass the run should pay once: mining walks the whole repository,
# and corroboration reads module summaries out of the store.


async def test_the_glossary_is_handed_the_corroboration_corpus(tmp_path):
    run = _run(tmp_path)
    await build_level8_coros(run)

    corpus = run.gen.onboarding_signals[0].module_corroboration
    assert corpus
    # Title first, summary under it -- what makes a match mean something.
    assert corpus[0].startswith("Blast Radius Evaluation")


async def test_the_corroboration_corpus_is_built_once_for_both_levels(tmp_path):
    """Level 6 and level 8 both want it, and it costs a batched store read."""
    store = _Store({"src": "Evaluates the blast radius of a change."})
    run = _run(tmp_path, written={}, store=store)

    await build_level6_coros(run)
    asked_after_overview = list(store.asked)
    await build_level8_coros(run)

    assert asked_after_overview, "the overview should have read the store"
    assert store.asked == asked_after_overview, "level 8 read the store a second time"


async def test_a_run_with_no_glossary_page_does_not_build_the_corpus(tmp_path):
    """A scoped run that asked for one onboarding page should not pay for a
    store read that only the glossary consumes."""
    store = _Store({"src": "Evaluates the blast radius of a change."})
    run = _run(tmp_path, store=store)
    run._emit = lambda page_id: "glossary" not in page_id

    await build_level8_coros(run)

    assert store.asked == []
    assert all(s.module_corroboration == () for s in run.gen.onboarding_signals)


async def test_the_overview_is_handed_the_repositorys_own_prose(tmp_path):
    """The front page's only natural-language input, wired at the level.

    Structure keeps every path and count; this supplies the words. Without it
    the payload is entirely structural and nothing in the prompt says what the
    product is for.
    """
    run = _run(tmp_path)
    await build_level6_coros(run)

    digest = run.gen.overview_kwargs["prose_digest"]
    assert "## Blast radius" in digest
    assert "Blast radius is the set of files a change can reach" in digest


async def test_a_repository_with_no_path_still_generates_an_overview(tmp_path):
    """``repo_path`` is optional on the run, and an absent one is not a crash."""
    run = _run(tmp_path)
    run.repo_path = None
    assert await build_level6_coros(run)
    assert run.gen.overview_kwargs["prose_digest"] == ""
