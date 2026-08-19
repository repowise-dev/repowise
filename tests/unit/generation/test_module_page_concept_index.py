"""A module page carries its members' identifiers, not only prose about them.

Module pages are the one page type written entirely by the model, and they come
out as prose: across a real run of 89 of them, four carried a fenced code block
at all. A reader who has just read "the resolver context" has no identifier to
grep for, and an identifier-exact query matches the description of the code
rather than the code.

The concept index closes that. It is rendered from the assembled symbol data
and appended after the model has written the page, so the identifiers and the
paths in it are the index's, not the model's, and no provider response can
change them. That placement is the whole point: put the same table in the
prompt and the model may reformat it, abbreviate a path, or drop it.

A page whose members export nothing renders no table rather than an empty one —
a header row with no rows under it is a claim that the module has no public
surface, made in the same shape as a real answer.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from repowise.core.generation.context.assembler import build_concept_index
from repowise.core.generation.context_assembler import ContextAssembler, FilePageContext
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.providers.llm.mock import MockProvider

CONCEPT_INDEX_HEADING = "## Concept index"


class _ProseOnlyProvider(MockProvider):
    """A provider whose answer names no symbol and no path.

    Every identifier in the assertions below therefore has exactly one possible
    source: the deterministic append. A mock that echoed part of its prompt
    would make this test pass on a page the model wrote itself.
    """

    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        response.content = (
            "# Resolution Layer\n\n"
            "The resolution layer turns cross-file references into edges the "
            "rest of the pipeline can walk. It sits between parsing and "
            "persistence and owns nothing else.\n"
        )
        return response


def _symbol(name: str, *, visibility: str = "public", parent: str = "", line: int = 1) -> dict:
    return {
        "name": name,
        "qualified_name": name,
        "kind": "function",
        "signature": f"def {name}()",
        "docstring": "",
        "visibility": visibility,
        "is_async": False,
        "complexity_estimate": 1,
        "decorators": [],
        "parent_name": parent,
        "start_line": line,
        "end_line": line + 4,
    }


def _file_context(path: str, symbols: list[dict], *, pagerank: float = 0.1) -> FilePageContext:
    return FilePageContext(
        file_path=path,
        language="python",
        docstring=None,
        symbols=symbols,
        imports=[],
        exports=[],
        pagerank_score=pagerank,
        betweenness_score=0.0,
        community_id=0,
        dependents=[],
        dependencies=[],
        is_api_contract=False,
        is_entry_point=False,
        is_test=False,
        parse_errors=[],
        estimated_tokens=10,
    )


@pytest.fixture
def prose_only_generator(sample_config) -> PageGenerator:
    return PageGenerator(_ProseOnlyProvider(), ContextAssembler(sample_config), sample_config)


@dataclass
class _Fixture:
    contexts: list[FilePageContext]


@pytest.fixture
def resolver_module() -> _Fixture:
    """Two files whose public symbols are the identifiers a reader would grep."""
    return _Fixture(
        contexts=[
            _file_context(
                "packages/core/src/repowise/core/ingestion/resolvers/context.py",
                [
                    _symbol("ResolverContext", line=20),
                    _symbol("resolve_imports", line=60),
                    _symbol("_private_helper", visibility="private", line=90),
                ],
                pagerank=0.4,
            ),
            _file_context(
                "packages/core/src/repowise/core/ingestion/resolvers/tsconfig.py",
                [_symbol("TsconfigResolver", line=12)],
                pagerank=0.1,
            ),
        ]
    )


async def _module_page(gen: PageGenerator, contexts: list[FilePageContext]):
    import networkx as nx

    return await gen.generate_module_page(
        "Resolution Layer",
        "python",
        contexts,
        nx.DiGraph(),
        target_path="packages/core/src/repowise/core/ingestion/resolvers",
        structural_key="k",
    )


async def test_rendered_page_carries_real_symbol_names_and_paths(
    prose_only_generator, resolver_module
):
    """The identifiers reach the page the model wrote, spelled exactly."""
    page = await _module_page(prose_only_generator, resolver_module.contexts)

    assert CONCEPT_INDEX_HEADING in page.content
    assert "| Concept | Symbol | File |" in page.content
    assert "`ResolverContext`" in page.content
    assert "`resolve_imports`" in page.content
    assert "`TsconfigResolver`" in page.content
    assert "`packages/core/src/repowise/core/ingestion/resolvers/context.py`" in page.content
    assert "`packages/core/src/repowise/core/ingestion/resolvers/tsconfig.py`" in page.content


async def test_concept_column_spells_the_identifier_as_prose(prose_only_generator, resolver_module):
    """The bridge the table exists to build: prose noun on the left, grep target
    on the right, both on one line so either wording finds the page."""
    page = await _module_page(prose_only_generator, resolver_module.contexts)

    assert "| Resolver context | `ResolverContext` |" in page.content
    assert "| Resolve imports | `resolve_imports` |" in page.content


async def test_non_public_symbols_stay_off_the_table(prose_only_generator, resolver_module):
    page = await _module_page(prose_only_generator, resolver_module.contexts)

    assert "_private_helper" not in page.content


async def test_a_module_with_no_public_symbols_renders_no_table(prose_only_generator):
    """No table at all, not an empty one."""
    contexts = [
        _file_context("pkg/a.py", [_symbol("_hidden", visibility="private")]),
        _file_context("pkg/b.py", []),
    ]

    page = await _module_page(prose_only_generator, contexts)

    assert CONCEPT_INDEX_HEADING not in page.content
    assert "| Concept | Symbol | File |" not in page.content


async def test_the_table_starts_on_its_own_line(prose_only_generator, resolver_module):
    """Appending is string concatenation, and a heading glued to the last line
    of the model's prose renders as body text. Assert the separator that has to
    be there, not merely the absence of extra ones."""
    page = await _module_page(prose_only_generator, resolver_module.contexts)

    assert f"\n\n{CONCEPT_INDEX_HEADING}\n" in page.content
    assert "\n\n\n" not in page.content


def test_concept_index_orders_by_the_page_rank_of_the_file():
    """The reader's first rows are the module's most-depended-on file."""
    rows, omitted = build_concept_index(
        [
            _file_context("pkg/low.py", [_symbol("LowValue")], pagerank=0.01),
            _file_context("pkg/high.py", [_symbol("HighValue")], pagerank=0.9),
        ]
    )

    assert [r["symbol"] for r in rows] == ["HighValue", "LowValue"]
    assert omitted == 0


def test_concept_index_caps_its_length_and_says_how_many_it_dropped():
    """A module with hundreds of exports would otherwise bury the page it is
    attached to. The count is reported so a truncated table cannot read as a
    complete public surface."""
    symbols = [_symbol(f"symbol_number_{i}", line=i) for i in range(80)]
    rows, omitted = build_concept_index([_file_context("pkg/wide.py", symbols)])

    assert len(rows) + omitted == 80
    assert omitted > 0


def test_duplicate_identifiers_across_files_each_keep_their_own_row():
    """Two files can export the same name; collapsing them would send a reader
    to one arbitrary file."""
    rows, _ = build_concept_index(
        [
            _file_context("pkg/a.py", [_symbol("handler")], pagerank=0.5),
            _file_context("pkg/b.py", [_symbol("handler")], pagerank=0.4),
        ]
    )

    assert [(r["symbol"], r["file"]) for r in rows] == [
        ("handler", "pkg/a.py"),
        ("handler", "pkg/b.py"),
    ]


@pytest.mark.parametrize(
    ("identifier", "concept"),
    [
        ("ResolverContext", "Resolver context"),
        ("resolve_imports", "Resolve imports"),
        ("EMBED_BATCH_MAX_ITEMS", "Embed batch max items"),
        ("HTTPAdapter", "HTTP adapter"),
        ("parse", "Parse"),
    ],
)
def test_concept_wording_is_derived_from_the_identifier(identifier, concept):
    """Derived, never written: a model-written column could describe a symbol
    that is not there, which is the failure this table exists to rule out."""
    rows, _ = build_concept_index([_file_context("pkg/a.py", [_symbol(identifier)])])

    assert rows[0]["concept"] == concept
