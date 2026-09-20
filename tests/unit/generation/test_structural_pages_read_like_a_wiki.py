"""What the deterministic pages say now that they render what they were given.

``FilePageContext`` has carried git history, co-change partners and decision
records since long before this suite; ``file_page.j2`` rendered none of them,
and a page of twenty-five import paths was the result. These tests pin what a
page says when it has something to say, that it says nothing when it does not,
and — the constraint that shapes all of it — that nothing a reader stops
seeing has left ``content``, which is the same string FTS indexes, the vector
store embeds and ``get_context`` returns verbatim.

Everything here renders the real template. A page's markdown is not something
to type out and compare against: Jinja whitespace control decides where the
newlines land, and a hand-written expectation agreed with a renderer that was
emitting different bytes once already.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from repowise.core.generation.context_assembler import (
    ContextAssembler,
    FilePageContext,
    SymbolSpotlightContext,
)
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.providers.llm.template import TemplateProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
READER_PERSONA_TS = REPO_ROOT / "packages" / "ui" / "src" / "docs" / "reader-persona.ts"


@pytest.fixture
def generator() -> PageGenerator:
    config = GenerationConfig(deterministic=True)
    return PageGenerator(TemplateProvider(), ContextAssembler(config), config)


def _symbol(name: str, kind: str = "function", **over) -> dict:
    return {
        "name": name,
        "qualified_name": f"pkg.mod.{name}",
        "kind": kind,
        "signature": over.pop("signature", f"def {name}() -> None"),
        "docstring": None,
        "visibility": over.pop("visibility", "public"),
        "is_async": False,
        "complexity_estimate": 1,
        "decorators": [],
        "parent_name": over.pop("parent_name", None),
        "start_line": 1,
        "end_line": 2,
    }


def _context(**over) -> FilePageContext:
    base = dict(
        file_path="pkg/mod/walk.py",
        language="python",
        docstring="Walks a repository tree.",
        symbols=[_symbol("walk_repo")],
        imports=[],
        exports=[],
        pagerank_score=0.1,
        betweenness_score=0.0,
        community_id=0,
        dependents=[],
        dependencies=[],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=0,
    )
    base.update(over)
    return FilePageContext(**base)


def render(generator: PageGenerator, ctx: FilePageContext) -> str:
    return generator._render("file_page.j2", style_prefix=False, ctx=ctx)


GIT = {
    "commit_count_total": 27,
    "commit_count_90d": 9,
    "last_commit_at": "2026-08-17 05:38:14.000000",
    "primary_owner_name": "Ada Lovelace",
    "primary_owner_commit_pct": 0.62,
    "prior_defect_count": 12,
    "is_hotspot": True,
}


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


class TestHistory:
    def test_it_says_what_the_git_history_knows(self, generator):
        page = render(generator, _context(git_metadata=GIT))

        assert "## History" in page
        assert "27 commits in its history, 9 in the last 90 days." in page
        assert "Ada Lovelace" in page
        assert "62%" in page
        assert "12 of those commits fixed a bug." in page
        assert "change hotspots" in page

    def test_the_date_is_absolute_so_the_page_does_not_move_on_its_own(self, generator):
        # The rendered bytes are this page's reuse key. "three days ago" would
        # restate every page in the wiki every day.
        page = render(generator, _context(git_metadata=GIT))

        assert "2026-08-17" in page
        assert "05:38:14" not in page

    def test_a_repository_with_no_history_renders_no_heading(self, generator):
        assert "## History" not in render(generator, _context())
        assert "## History" not in render(generator, _context(git_metadata={}))

    def test_a_fix_count_that_outruns_the_commit_log_is_left_out(self, generator):
        # The two are counted over different windows, so a file whose fixes
        # outnumber its own commits has been moved and the fixes belong to a
        # path this page no longer describes.
        page = render(
            generator,
            _context(git_metadata={"commit_count_total": 1, "prior_defect_count": 2}),
        )

        assert "## History" in page
        assert "fixed a bug" not in page

    def test_one_commit_is_not_described_in_the_plural(self, generator):
        page = render(generator, _context(git_metadata={"commit_count_total": 1}))

        assert "1 commit in its history" in page


class TestChangesTogetherWith:
    def test_it_names_the_partner_and_how_often(self, generator):
        page = render(
            generator,
            _context(
                co_change_pages=[
                    {"path": "pkg/mod/persist.py", "commits": 28, "last": "2026-08-29"}
                ]
            ),
        )

        assert "## Changes together with" in page
        assert "`pkg/mod/persist.py`" in page
        assert "28 shared commits" in page
        assert "2026-08-29" in page

    def test_no_partners_renders_no_heading(self, generator):
        assert "## Changes together with" not in render(generator, _context())


class TestDecisions:
    def test_it_carries_the_decision_and_the_reason_for_it(self, generator):
        page = render(
            generator,
            _context(
                decision_records=[
                    {
                        "title": "Prune at traversal time",
                        "decision": "Directories are pruned in `dirnames[:]`, never post-hoc.",
                        "rationale": "An unpruned walk over vendored repos took minutes.",
                        "source": "inline",
                        "confidence": 0.9,
                        "evidence_file": "pkg/mod/walk.py",
                    }
                ]
            ),
        )

        assert "## Decisions touching this file" in page
        assert "Prune at traversal time" in page
        assert "never post-hoc" in page
        assert "took minutes" in page

    def test_no_decisions_renders_no_heading(self, generator):
        assert "## Decisions touching this file" not in render(generator, _context())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class TestPublicApi:
    def test_module_bookkeeping_is_not_listed_as_api(self, generator):
        ctx = _context(
            symbols=[
                _symbol("log", "variable", signature="log = structlog.get_logger(__name__)"),
                _symbol("__all__", "variable", signature='__all__ = ["walk_repo"]'),
                _symbol("__init__", "method", parent_name="WalkSnapshot"),
                _symbol("walk_repo"),
                _symbol("SourceFile", "variable", signature="SourceFile = tuple[str, str]"),
            ]
        )
        table = render(generator, ctx).split("## Public API", 1)[1].split("Also defined", 1)[0]

        assert "`walk_repo`" in table
        # A capitalised module-level variable is a type alias, not module state.
        assert "`SourceFile`" in table
        for demoted in ("`log`", "`__all__`", "`__init__`"):
            assert demoted not in table

    def test_a_demoted_name_is_still_spelled_on_the_page(self, generator):
        # ``content`` is the index entry as well as the page. A name the table
        # stops carrying must not stop being findable.
        ctx = _context(
            symbols=[
                _symbol("log", "variable", signature="log = structlog.get_logger(__name__)"),
                _symbol("walk_repo"),
            ]
        )
        page = render(generator, ctx)

        # The signature, not the bare name: ``log`` alone would take
        # ``structlog``, ``get_logger`` and ``__name__`` off the page, and the
        # page is the index entry.
        assert "Also defined: `log = structlog.get_logger(__name__)`." in page

    def test_a_file_that_declares_only_bookkeeping_still_names_it(self, generator):
        ctx = _context(
            symbols=[_symbol("__all__", "variable", signature='__all__ = ["walk_repo"]')]
        )
        page = render(generator, ctx)

        assert "## Public API" in page
        assert '`__all__ = ["walk_repo"]`' in page

    def test_the_signature_column_reads_as_a_declaration(self, generator):
        ctx = _context(
            symbols=[
                _symbol(
                    "walk_repo",
                    signature=(
                        "def walk_repo(\n    root: Path | str,\n    *,\n"
                        "    prune_dirs: frozenset[str] = PRUNED_DIRS,\n"
                        "    prune_nested_git: bool = True,\n"
                        "    max_depth: int = 64,\n"
                        ") -> Iterator[tuple[Path, list[str], list[str]]]"
                    ),
                )
            ]
        )
        row = next(ln for ln in render(generator, ctx).splitlines() if ln.startswith("| `walk_repo`"))

        assert "( root" not in row
        assert row.rstrip(" |").endswith("-> Iterator[tuple[Path, list[str], list[str]]]")


# ---------------------------------------------------------------------------
# Dependency lists and the layer role
# ---------------------------------------------------------------------------


DEPS = [
    "pkg/resolvers/go.py",
    "pkg/resolvers/ruby.py",
    "pkg/resolvers/swift.py",
    "pkg/pipeline/run.py",
]


class TestDependencyGrouping:

    def test_paths_are_grouped_by_directory_busiest_first(self, generator):
        section = render(generator, _context(dependencies=DEPS)).split("## Depends on", 1)[1]
        headings = [ln for ln in section.splitlines() if ln.startswith("**`")]

        assert headings[:2] == ["**`pkg/resolvers`**", "**`pkg/pipeline`**"]

    def test_every_path_is_still_printed_whole(self, generator):
        page = render(generator, _context(dependencies=DEPS, dependents=DEPS))

        for path in DEPS:
            assert f"- `{path}`" in page

    def test_grouping_leaves_no_gap_between_sections(self, generator):
        page = render(
            generator,
            _context(dependencies=DEPS, dependents=DEPS, git_metadata=GIT),
        )

        assert "\n\n\n" not in page


class TestLayerRole:
    @pytest.mark.parametrize(
        "role,expected",
        [
            ("edge_connector", "boundary"),
            ("entry_point", "entry point"),
            ("internal", "internal to its layer"),
        ],
    )
    def test_the_role_is_spelled_for_a_reader(self, generator, role, expected):
        page = render(generator, _context(kg_layer_name="Core Pipeline", kg_layer_role=role))

        assert expected in page
        assert "edge_connector" not in page


class TestOverview:
    def test_a_file_with_no_docstring_borrows_the_graph_summary(self, generator):
        page = render(
            generator,
            _context(docstring=None, kg_node_summary="Prunes junk directories during a walk."),
        )

        assert "Prunes junk directories during a walk." in page
        # The structural sentence still follows it: it is what names the
        # language and the layer.
        assert "is a python source file" in page

    def test_a_docstring_still_wins(self, generator):
        page = render(generator, _context(kg_node_summary="Machine summary."))

        assert "Walks a repository tree." in page
        assert "Machine summary." not in page


# ---------------------------------------------------------------------------
# The constraint: nothing leaves ``content``
# ---------------------------------------------------------------------------


def test_no_identifier_the_context_carried_leaves_the_page(generator):
    """The page is the index entry, so a dropped identifier is a lost query.

    Pinned against the context rather than against a stored copy of the old
    markdown: an expectation derived from the inputs keeps failing for the
    right reason after the next template edit, where a golden file would only
    say that the bytes moved.
    """
    ctx = _context(
        docstring=None,
        kg_node_summary="Prunes junk directories during a walk.",
        symbols=[
            _symbol("walk_repo"),
            _symbol("log", "variable", signature="log = structlog.get_logger(__name__)"),
            _symbol("__all__", "variable"),
            _symbol("__init__", "method", parent_name="WalkSnapshot"),
        ],
        dependencies=["pkg/resolvers/go.py", "pkg/pipeline/run.py"],
        dependents=["pkg/cli/main.py"],
        git_metadata=GIT,
        co_change_pages=[{"path": "pkg/mod/persist.py", "commits": 28, "last": "2026-08-29"}],
        decision_records=[
            {
                "title": "Prune at traversal time",
                "decision": "Pruned in `dirnames[:]`.",
                "rationale": "",
                "source": "inline",
                "confidence": 0.9,
                "evidence_file": "pkg/mod/walk.py",
            }
        ],
        kg_layer_name="Core Pipeline",
        kg_layer_role="edge_connector",
        file_vocabulary="prune_dirs max_depth node_modules",
    )
    page = render(generator, ctx)

    expected = (
        {ctx.file_path}
        | set(ctx.dependencies)
        | set(ctx.dependents)
        | {s["name"] for s in ctx.symbols}
        | {cc["path"] for cc in ctx.co_change_pages}
        | set(ctx.file_vocabulary.split())
    )
    missing = sorted(name for name in expected if name not in page)

    assert not missing, missing


def test_the_page_says_nothing_it_was_not_given(generator):
    """Every section below the Overview is conditional, so a bare context
    renders a page with no empty headings on it."""
    page = render(generator, _context(docstring=None, symbols=[]))

    for heading in (
        "## History",
        "## Changes together with",
        "## Decisions touching this file",
        "## Public API",
        "## Depends on",
        "## Used by",
        "## Usage Notes",
    ):
        assert heading not in page


# ---------------------------------------------------------------------------
# The reader lens
# ---------------------------------------------------------------------------


def _contributor_hide(source: str) -> set[str]:
    """The heading keywords the default reader persona drops."""
    body = source.split("const CONTRIBUTOR_HIDE", 1)[1].split("]", 1)[0]
    return set(re.findall(r'"([^"]+)"', body))


def test_the_scaffolding_headings_are_the_ones_the_reader_lens_hides(generator):
    """The template and ``reader-persona.ts`` have to agree, byte for byte.

    ``## In the code`` and ``## Questions this page answers`` exist for
    retrieval: the vocabulary bag is the only part of the page written in the
    words a question uses, and the question block is the only part shaped like
    one. Neither can be dropped here without dropping it from the index, so
    the reader persona hides them client-side instead — and it matches on the
    heading text, lowercased, which is a string in another language's source
    file. The headings come out of a real render for that reason.
    """
    ctx = _context(
        dependents=["pkg/cli/main.py"],
        file_vocabulary="prune_dirs max_depth",
    )
    page = render(generator, ctx)
    rendered = {ln[3:].strip().lower() for ln in page.splitlines() if ln.startswith("## ")}
    assert {"in the code", "questions this page answers"} <= rendered

    hidden = _contributor_hide(READER_PERSONA_TS.read_text(encoding="utf-8"))

    assert {"in the code", "questions this page answers"} <= hidden


# ---------------------------------------------------------------------------
# Symbol spotlight
# ---------------------------------------------------------------------------


def _spotlight(**over) -> SymbolSpotlightContext:
    base = dict(
        symbol_name="PRUNED_DIRS",
        qualified_name="pkg.mod.walk.PRUNED_DIRS",
        kind="constant",
        signature="PRUNED_DIRS: frozenset[str]",
        docstring=None,
        file_path="pkg/mod/walk.py",
        decorators=[],
        is_async=False,
        complexity_estimate=0,
        callers=[],
    )
    base.update(over)
    return SymbolSpotlightContext(**base)


class TestSymbolSpotlight:
    def test_the_title_is_the_symbol_not_a_dotted_path_of_directories(self, generator):
        page = generator._structural_symbol_spotlight(
            _spotlight(), "pkg/mod/walk.py::PRUNED_DIRS", "Symbol: pkg/mod/walk.py::PRUNED_DIRS"
        )

        assert page.content.startswith("# PRUNED_DIRS\n")
        # The file is on the line under it, which is where a reader looks.
        assert "`pkg/mod/walk.py`" in page.content
        # And the dotted name stays on the page, because it is an identifier
        # the index carries.
        assert "pkg.mod.walk.PRUNED_DIRS" in page.content

    def test_a_resolved_call_is_reported_as_a_call(self, generator):
        page = generator._structural_symbol_spotlight(
            _spotlight(
                callers=["pkg/cli/main.py"],
                call_sites=[{"caller": "walk_repo", "caller_file": "pkg/mod/walk.py"}],
            ),
            "pkg/mod/walk.py::PRUNED_DIRS",
            "Symbol: pkg/mod/walk.py::PRUNED_DIRS",
        )

        used = page.content.split("## Where it is used", 1)[1].split("\n## ", 1)[0]
        assert "Reached by 1 resolved call." in used
        assert "`walk_repo`" in used
        assert "not confirmed call sites" not in used
        assert "What calls `PRUNED_DIRS`?" in page.content

    def test_the_importers_move_under_a_heading_the_reader_lens_hides(self, generator):
        # They are the fifteen thousand file paths this change would otherwise
        # take out of the index, so they stay on the page and leave the view.
        page = generator._structural_symbol_spotlight(
            _spotlight(
                callers=["pkg/cli/main.py"],
                call_sites=[{"caller": "walk_repo", "caller_file": "pkg/mod/walk.py"}],
            ),
            "pkg/mod/walk.py::PRUNED_DIRS",
            "Symbol: pkg/mod/walk.py::PRUNED_DIRS",
        )

        assert "## Files importing this module" in page.content
        importers = page.content.split("## Files importing this module", 1)[1]
        assert "`pkg/cli/main.py`" in importers

        source = READER_PERSONA_TS.read_text(encoding="utf-8")
        hidden = _contributor_hide(source)
        assert "files importing this module" in hidden

    def test_the_count_is_the_real_total_not_the_printed_one(self, generator):
        page = generator._structural_symbol_spotlight(
            _spotlight(
                call_sites=[
                    {"caller": f"caller_{i}", "caller_file": f"pkg/c{i}.py"} for i in range(30)
                ]
            ),
            "pkg/mod/walk.py::PRUNED_DIRS",
            "Symbol: pkg/mod/walk.py::PRUNED_DIRS",
        )

        assert "Reached by 30 resolved calls." in page.content
        assert "and 5 more." in page.content

    def test_an_unresolved_symbol_keeps_the_honest_import_wording(self, generator):
        page = generator._structural_symbol_spotlight(
            _spotlight(callers=["pkg/cli/main.py"]),
            "pkg/mod/walk.py::PRUNED_DIRS",
            "Symbol: pkg/mod/walk.py::PRUNED_DIRS",
        )

        assert "not confirmed call sites" in page.content
        assert "`pkg/cli/main.py`" in page.content
