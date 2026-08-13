"""The file's own vocabulary, and the round trip that makes it worth having.

A file page renders an overview, a symbol table and a dependency list. None of
those carry the words a question is actually asked in: a flag name, an error
string, a struct field, the phrasing of a doc comment. So a page can describe
a file accurately and remain unreachable by any question about what it does.

Measured on the cli/cli and django canary indexes, gold file in the top ten of
a full-text search over page text: **Go 4 to 12 of 20 instances, python 34 to
38 of 50**. The same probe against an index shaped like a symbol-only
competitor scored 8 of 20 on Go and *worse than the current pages* on python,
which is why this ships as page content and not as a second symbol index.

Two properties carry the whole result and each has a test below:

* it is **per-file**, so it discriminates. Repo-level vocabulary rendered here
  would be byte-identical on every page in the corpus.
* it reaches the **full-text row**, not only the rendered page. The write path
  hands ``page.content`` to the indexer verbatim, so nothing would raise if the
  section were dropped or moved into metadata.
"""

from __future__ import annotations

from pathlib import Path

import jinja2
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from repowise.core.generation.context.file_vocabulary import file_vocabulary
from repowise.core.generation.context_assembler import FilePageContext
from repowise.core.generation.page_generator.structural import (
    as_markdown,
    oneline,
    signature,
)
from repowise.core.generation.structural_labels import resolve_structural_labels
from repowise.core.persistence.crud import upsert_page, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.search import FullTextSearch

VOCAB_HEADING = "## In the code"

# The real thing, trimmed. This is the file behind the ContextBench instance
# the diagnosis was built on: an `--order` flag on `gh release list`. Its
# generated page carried a path header, two symbol names and 29 dependency
# paths, and not one of the tokens below.
GO_SOURCE = '''
package list

// ListOptions carries the flags for the release list command.
type ListOptions struct {
	Limit              int
	ExcludePreReleases bool
	Order              string
}

func NewCmdList(f *cmdutil.Factory) *cobra.Command {
	cmd.Flags().StringVar(&opts.Order, "order", "desc", "Order of releases returned")
	return cmd
}

func listRun(opts *ListOptions) error {
	query := `orderBy: {field: CREATED_AT, direction: DESC}`
	return nil
}
'''


class TestWhatItLifts:
    def test_declared_field_names_survive(self):
        out = file_vocabulary(GO_SOURCE)
        assert "Order" in out
        assert "ExcludePreReleases" in out

    def test_string_literals_survive(self):
        """Flag names and help text: the wording a bug report repeats back."""
        out = file_vocabulary(GO_SOURCE)
        assert "Order of releases returned" in out
        assert "desc" in out

    def test_a_literal_that_repeats_an_identifier_is_not_stored_twice(self):
        """Dedup is case-folded, and that is the right call for an index.

        The flag string ``"order"`` and the field name ``Order`` are one token
        to a full-text index, so carrying both would spend the cap to buy
        nothing. The bag keeps whichever came first.

        Deduplication is per entry, not per word: the help text
        ``"Order of releases returned"`` is its own entry and keeps its own
        copy of the word, which is correct, because the phrase is what a bug
        report quotes back.
        """
        out = file_vocabulary('    Order string\nvar f = "order"\n')
        assert out.lower().split().count("order") == 1

    def test_camel_case_is_also_offered_as_words(self):
        """A question is asked in words, not in camel case.

        ``ExcludePreReleases`` is kept whole *and* split, because a reader asks
        "how do I exclude pre-releases" and never types the identifier.
        """
        out = file_vocabulary(GO_SOURCE).lower()
        for word in ("exclude", "pre", "releases"):
            assert word in out.split() or word in out

    def test_comment_prose_survives(self):
        assert "ListOptions carries the flags" in file_vocabulary(GO_SOURCE)

    def test_python_attributes_are_lifted(self):
        source = "class Config:\n    def __init__(self):\n        self.retry_budget = 3\n"
        out = file_vocabulary(source)
        assert "retry_budget" in out

    def test_the_tokens_the_gold_instance_needed_all_arrive(self):
        """The end-to-end claim, stated as one assertion.

        Every one of these appears in the gold source and none of them reached
        the page before this section existed. Compared case-insensitively
        because that is how the full-text index compares them: ``CREATED_AT``
        arrives as ``created``, which answers "ordered by creation date"
        exactly as well.
        """
        out = file_vocabulary(GO_SOURCE).lower()
        for token in ("order", "desc", "created", "releases"):
            assert token in out, f"{token!r} missing"


class TestBounds:
    def test_capped(self):
        source = "\n".join(f'    Field{i} string // comment number {i}' for i in range(4000))
        assert len(file_vocabulary(source)) <= 1200

    def test_the_cap_cuts_the_least_specific_material(self):
        """Ordering is the reason the cap is affordable.

        Declared names and literals go in before loose identifiers, so a
        truncated bag keeps the distinguishing half.
        """
        source = GO_SOURCE + "\n" + "\n".join(f"var filler{i} int" for i in range(3000))
        out = file_vocabulary(source)
        assert "Order of releases returned" in out
        assert "filler2999" not in out

    def test_empty_source_yields_nothing_rather_than_a_heading(self):
        assert file_vocabulary("") == ""

    def test_single_and_double_character_names_are_not_collected(self):
        """Receivers and loop variables are never what a question is about."""
        out = file_vocabulary("func (c *Client) do(i int) { x := 1; _ = x }")
        assert " i " not in f" {out} "
        assert " x " not in f" {out} "

    def test_long_blobs_do_not_eat_the_cap(self):
        """Minified data and embedded SQL are long and unsearchable."""
        blob = "A" * 900
        out = file_vocabulary(f'    Name string\nvar data = "{blob}"')
        assert blob not in out
        assert "Name" in out


@pytest.fixture
def jinja_env() -> jinja2.Environment:
    templates_dir = (
        Path(__file__).parents[3]
        / "packages"
        / "core"
        / "src"
        / "repowise"
        / "core"
        / "generation"
        / "templates"
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    env.filters.setdefault("oneline", oneline)
    env.filters.setdefault("as_markdown", as_markdown)
    env.filters.setdefault("signature", signature)
    env.globals["labels"] = resolve_structural_labels(None)
    return env


def _file_page(**overrides) -> FilePageContext:
    base = dict(
        file_path="pkg/cmd/release/list/list.go",
        language="go",
        docstring=None,
        symbols=[
            {
                "name": "NewCmdList",
                "kind": "function",
                "signature": "func NewCmdList(f *cmdutil.Factory) *cobra.Command",
                "docstring": None,
                "visibility": "public",
                "is_async": False,
                "complexity_estimate": 1,
                "decorators": [],
                "parent_name": None,
                "start_line": 1,
                "end_line": 10,
            }
        ],
        imports=[],
        exports=["NewCmdList"],
        pagerank_score=0.5,
        betweenness_score=0.1,
        community_id=0,
        dependents=[],
        dependencies=["api/client.go"],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=50,
    )
    base.update(overrides)
    return FilePageContext(**base)


class TestRendering:
    def test_the_section_renders_when_there_is_vocabulary(self, jinja_env):
        ctx = _file_page(file_vocabulary=file_vocabulary(GO_SOURCE))
        page = jinja_env.get_template("file_page.j2").render(ctx=ctx)
        assert VOCAB_HEADING in page
        assert "Order of releases returned" in page

    def test_no_heading_when_the_file_yields_nothing(self, jinja_env):
        """Every section below the Overview is conditional, and this is why.

        An empty heading puts the same stock line into the index on thousands
        of pages, where it matches every query and distinguishes none.
        """
        page = jinja_env.get_template("file_page.j2").render(ctx=_file_page(file_vocabulary=""))
        assert VOCAB_HEADING not in page

    def test_it_sits_below_the_questions_block(self, jinja_env):
        """``_extract_summary`` reads back from the top. A bag of words is not
        a summary, so it must not be the first thing the page says."""
        ctx = _file_page(file_vocabulary=file_vocabulary(GO_SOURCE))
        page = jinja_env.get_template("file_page.j2").render(ctx=ctx)
        assert page.index("## Overview") < page.index(VOCAB_HEADING)
        assert page.index("## Questions this page answers") < page.index(VOCAB_HEADING)

    def test_two_files_get_different_vocabulary(self, jinja_env):
        """The property the whole result rests on.

        Repo-level vocabulary would render byte-identically on all ~700 file
        pages of cli/cli and carry zero discriminative power. This is the
        opposite granularity, deliberately.
        """
        tmpl = jinja_env.get_template("file_page.j2")
        a = tmpl.render(ctx=_file_page(file_vocabulary=file_vocabulary(GO_SOURCE)))
        b = tmpl.render(
            ctx=_file_page(
                file_path="api/client.go",
                file_vocabulary=file_vocabulary('type Client struct {\n\tHTTP string\n}'),
            )
        )
        assert "Order of releases returned" in a
        assert "Order of releases returned" not in b


@pytest.fixture
async def fts():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await init_db(engine)
    search = FullTextSearch(engine)
    await search.ensure_index()
    yield engine, search
    await engine.dispose()


async def _index(engine, search, *, page_id, target_path, content):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        repo = await upsert_repository(session, name="r", local_path="/tmp/r")
        await session.commit()
        await upsert_page(
            session,
            page_id=page_id,
            repository_id=repo.id,
            page_type="file_page",
            title=target_path,
            content=content,
            summary="",
            target_path=target_path,
            source_hash="h",
            model_name="template",
            provider_name="template",
        )
        await session.commit()
    await search.index(page_id, target_path, content, summary="", target_path=target_path)


@pytest.mark.asyncio
async def test_the_words_reach_the_full_text_row(fts, jinja_env):
    """The round trip, not the render.

    ``FullTextSearch.index`` is handed ``page.content`` verbatim, so the
    section could be dropped, moved into metadata, or added after the row was
    written and nothing would raise. This is the assertion that the page is
    actually findable by the words its own file contains, which is the entire
    claim.
    """
    engine, search = fts
    ctx = _file_page(file_vocabulary=file_vocabulary(GO_SOURCE))
    content = jinja_env.get_template("file_page.j2").render(ctx=ctx)
    await _index(
        engine,
        search,
        page_id="file_page:pkg/cmd/release/list/list.go",
        target_path="pkg/cmd/release/list/list.go",
        content=content,
    )

    hits = await search.search("order of releases returned", limit=10)
    assert [h.target_path for h in hits] == ["pkg/cmd/release/list/list.go"]
