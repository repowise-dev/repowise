"""The overview's capability table, and the rule that decides its rows.

The selection rule is the whole of this feature. Mined vocabulary ranked by
document frequency alone is not front-page material: on this repository the
top of that list holds "DONE", "Architecture" and "Files changed" alongside
the real capabilities, and half the rows would be junk.

So a term reaches the page only with corroboration from a second,
independently-derived artifact — a module page, grouped from the dependency
graph and written from the code, has to name it. Nothing here is a stopword
list, and nothing here is tuned per repository.

Like the package table, the result is embedded after the page is written, so
the model page, the ``--no-prose`` page and the provider-outage fallback carry
identical bytes.
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from repowise.core.generation.concept_tree.vocabulary import HouseTerm
from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.overview_tables import (
    CAPABILITY_TABLE_HEADING,
    build_capability_table,
    embed_capability_table,
    select_capabilities,
)
from repowise.core.ingestion.models import PackageInfo, RepoStructure

from .conftest import _make_file_info


def _term(
    term: str,
    *,
    definition: str | None = None,
    definition_source: str | None = None,
    source_paths: tuple[str, ...] = ("README.md",),
    doc_frequency: int = 1,
    code_frequency: int = 5,
) -> HouseTerm:
    return HouseTerm(
        term=term,
        definition=definition,
        definition_source=definition_source,
        source_paths=source_paths,
        doc_frequency=doc_frequency,
        code_frequency=code_frequency,
        is_indexed_symbol=False,
    )


#: What the structural side calls the parts of the system: module group
#: titles, their community labels, and their paths. Cut from the dependency
#: graph and named from the code, with no knowledge of the documents.
MODULES = [
    "Dead Code and Reachability Analysis",
    "Blast Radius UI",
    "Ledger Postings",
    "src/analysis/dead_code",
]


# ---------------------------------------------------------------------------
# The selection rule
# ---------------------------------------------------------------------------


def test_a_term_no_module_page_names_does_not_reach_the_page():
    """The gate. "DONE" is a real heading in this repository's own documents
    and leads the frequency ranking; no module page has ever mentioned it."""
    picked = select_capabilities([_term("DONE"), _term("Dead code")], MODULES)
    assert [c.term for c in picked] == ["Dead code"]


def test_corroboration_may_come_from_a_title_or_from_a_path():
    """Both are what the structure calls a part of the system."""
    by_title = select_capabilities([_term("Blast radius")], MODULES)
    by_path = select_capabilities([_term("Dead code")], ["src/analysis/dead_code"])
    assert [c.term for c in by_title] == ["Blast radius"]
    assert [c.term for c in by_path] == ["Dead code"]


def test_multi_word_terms_come_before_single_word_ones():
    """A subsystem is nearly always named with two words and an ordinary
    English word with one. "Analysis" is corroborated — it is in a module
    title — and it is still not what the front page should lead with."""
    picked = select_capabilities([_term("Analysis"), _term("Dead code")], MODULES, limit=2)
    assert [c.term for c in picked] == ["Dead code", "Analysis"]


def test_a_single_word_still_reaches_the_table_when_there_is_room():
    """Ordering, not a filter. A repository whose vocabulary is single words
    -- django's is Models, Middleware, Migrations -- must still get a table."""
    picked = select_capabilities([_term("Analysis")], MODULES)
    assert [c.term for c in picked] == ["Analysis"]


def test_the_table_is_capped():
    terms = [_term(f"Term number {n}") for n in range(20)]
    modules = [" ".join(t.term for t in terms)]
    assert len(select_capabilities(terms, modules)) == 6


def test_selection_does_not_depend_on_the_order_of_the_corroboration_corpus():
    """The selection is a function of the inputs, not of how they arrived."""
    terms = [_term("Dead code"), _term("Blast radius"), _term("Postings")]
    forwards = select_capabilities(terms, MODULES)
    backwards = select_capabilities(terms, list(reversed(MODULES)))
    assert forwards == backwards


def test_a_term_with_no_path_anywhere_is_not_offered():
    """Every row cites where it was read. A row that cannot is a claim this
    wiki does not make."""
    assert select_capabilities([_term("Dead code", source_paths=())], MODULES) == []


def test_selection_is_logged_with_what_it_kept_and_what_it_dropped():
    with capture_logs() as logs:
        select_capabilities([_term("DONE"), _term("Dead code")], MODULES)
    event = next(e for e in logs if e["event"] == "house_vocabulary.selected")
    assert event["mined"] == 2
    assert event["corroborated"] == 1
    assert event["terms"] == ["Dead code"]


def test_mining_terms_that_no_module_page_corroborates_is_a_warning():
    """Distinct from mining nothing: the documents name things the structure
    does not. A real answer about a repository, and also what an empty
    corroboration corpus looks like, so the counts that separate them go out."""
    with capture_logs() as logs:
        select_capabilities([_term("DONE")], MODULES)
    assert any(e["event"] == "house_vocabulary.uncorroborated" for e in logs)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_the_table_carries_the_term_its_sentence_and_its_source():
    picked = select_capabilities(
        [
            _term(
                "Dead code",
                definition="Dead code is code no import path reaches.",
                definition_source="src/analysis/dead_code.py",
            )
        ],
        MODULES,
    )
    table = build_capability_table(picked)
    assert "| Capability | What it is | Where it is written |" in table
    assert "| Dead code | Dead code is code no import path reaches. |" in table
    assert "`src/analysis/dead_code.py`" in table


@pytest.mark.parametrize(
    "text",
    [
        # Both real, both from this repository's own front page.
        "repowise init [PATH]      # index a codebase (one-time; asks, or --no-prose -y needs no LLM)",
        "`repowise distill <cmd>` compresses command output *before* the agent reads it:",
        "$ repowise update --all",
        "Run it with --no-prose to skip the model",
        "cat wiki.db | sqlite3 .dump",
        "Two parts,",
        "See below",
    ],
)
def test_prose_that_is_not_a_statement_is_not_offered_as_a_definition(text):
    """A mined definition is whatever prose sat nearest the term, and near a
    term in a README that is often a command line or a lead-in. An em dash is
    a better answer: the reader learns the capability exists and is not
    misinformed about what it does."""
    picked = select_capabilities([_term("Dead code", definition=text)], MODULES)
    assert picked[0].definition is None


@pytest.mark.parametrize(
    "term,text",
    [
        ("Dead code", "Dead code is code no import path reaches."),
        (
            "Blast radius",
            "Blast radius is the set of files a change can reach through the import graph.",
        ),
        ("Middleware", "Middleware for utilizing Web-server-provided authentication."),
        # Terse, and still the repository's own answer. Both of these were
        # rejected by a first cut of the test that was tuned too strict.
        ("Blast radius", "Blast-radius request/response models."),
        (
            "Decisions",
            "Decisions are co-located in the page vector store under the "
            "``decision:<record_id>`` namespace (no separate table).",
        ),
    ],
)
def test_a_real_sentence_survives(term, text):
    """Each sentence is paired with the term it names, because a definition
    that never mentions its term is rejected as prose that merely sat nearby."""
    picked = select_capabilities([_term(term, definition=text)], [*MODULES, term])
    assert picked[0].definition == text


def test_a_rejected_definition_does_not_leave_its_source_behind():
    """Citing where prose the table declined to quote lives would point the
    reader at a line that is not on the page."""
    picked = select_capabilities(
        [
            _term(
                "Dead code",
                definition="$ repowise dead-code --json",
                definition_source="README.md#usage",
                source_paths=("docs/guide.md",),
            )
        ],
        MODULES,
    )
    assert picked[0].definition is None
    assert picked[0].source_path == "docs/guide.md"


def test_a_rejected_definition_is_logged():
    with capture_logs() as logs:
        select_capabilities([_term("Dead code", definition="$ repowise dead-code")], MODULES)
    assert any(e["event"] == "house_vocabulary.definition_rejected" for e in logs)


def test_a_term_the_repository_never_defined_still_gets_a_row():
    """Naming a capability and never writing a sentence about it is common,
    and inventing the sentence is the one thing the miner refuses to do."""
    table = build_capability_table(select_capabilities([_term("Dead code")], MODULES))
    assert "| Dead code | — | `README.md` |" in table


def test_the_source_falls_back_to_the_document_that_named_the_term():
    picked = select_capabilities(
        [_term("Dead code", source_paths=("docs/guide.md", "src/sweep.py"))], MODULES
    )
    assert picked[0].source_path == "docs/guide.md"


def test_a_pipe_in_mined_prose_does_not_break_the_table():
    """Definitions are the repository's text, not ours. One that quotes a
    shell pipeline or a reStructuredText grid row would otherwise shift every
    column to its right."""
    table = build_capability_table(
        select_capabilities(
            [_term("Dead code", definition="Run `repowise dead-code | less` to read it all.")],
            MODULES,
        )
    )
    row = next(line for line in table.splitlines() if line.startswith("| Dead code"))
    assert row.count("|") - row.count("\\|") == 4


def test_a_long_definition_is_cut_rather_than_wrapping_the_row():
    long_sentence = "Dead code is " + "unreachable " * 60 + "code."
    table = build_capability_table(
        select_capabilities([_term("Dead code", definition=long_sentence)], MODULES)
    )
    row = next(line for line in table.splitlines() if line.startswith("| Dead code"))
    assert "…" in row
    assert len(row) < 260


def test_no_capabilities_means_no_table_and_no_heading():
    """A header over an empty table says less than no section at all."""
    assert build_capability_table([]) is None
    assert embed_capability_table("# Overview\n", None) == "# Overview\n"


def test_the_empty_case_is_logged():
    with capture_logs() as logs:
        build_capability_table([])
    assert any(e["event"] == "overview_capability_table_empty" for e in logs)


def test_embedding_is_idempotent():
    """A reused or cached page picks up the current selection rather than
    accumulating a second one -- including over a section the model wrote."""
    table = build_capability_table(select_capabilities([_term("Dead code")], MODULES))
    once = embed_capability_table("# Overview\n\nSome prose.\n", table)
    twice = embed_capability_table(once, table)
    assert once == twice
    assert twice.count(CAPABILITY_TABLE_HEADING) == 1


def test_embedding_replaces_a_section_the_model_wrote_itself():
    model_page = "# Overview\n\n## What it does\n\nIt does several things.\n\n## Packages\n\nrows\n"
    table = build_capability_table(select_capabilities([_term("Dead code")], MODULES))
    out = embed_capability_table(model_page, table)
    assert "It does several things." not in out
    assert out.count(CAPABILITY_TABLE_HEADING) == 1
    # and the section after it survives
    assert "## Packages" in out


# ---------------------------------------------------------------------------
# Through the generator, on every path
# ---------------------------------------------------------------------------


def _pkg(name: str, language: str = "python") -> PackageInfo:
    return PackageInfo(
        name=name,
        path=f"packages/{name}",
        language=language,
        entry_points=[],
        manifest_file="pyproject.toml",
    )


def _parsed(path: str, language: str = "python"):
    from repowise.core.ingestion.models import ParsedFile

    return ParsedFile(
        file_info=_make_file_info(path, language=language), symbols=[], imports=[], exports=[]
    )


@pytest.fixture
def structure():
    return RepoStructure(
        is_monorepo=True,
        packages=[_pkg("core")],
        root_language_distribution={"python": 1.0},
        total_files=2,
        total_loc=200,
        entry_points=[],
    )


@pytest.fixture
def parsed_files():
    return [_parsed("packages/core/a.py"), _parsed("packages/core/b.py")]


@pytest.fixture
def capabilities():
    return select_capabilities(
        [
            _term(
                "Dead code",
                definition="Dead code is code no import path reaches.",
                definition_source="src/analysis/dead_code.py",
            )
        ],
        MODULES,
    )


async def test_the_deterministic_page_carries_the_table(structure, parsed_files, capabilities):
    """`repo_overview.j2` is a prompt, not a page. Asserting a table on it
    asserts nothing about what ships, so this goes through the generator."""
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    page = await gen.generate_repo_overview(
        structure,
        {},
        [],
        {},
        repo_name="testrepo",
        parsed_files=parsed_files,
        capabilities=capabilities,
    )

    assert "| Capability | What it is | Where it is written |" in page.content
    assert "Dead code is code no import path reaches." in page.content
    assert page.content.count(CAPABILITY_TABLE_HEADING) == 1


async def test_the_model_written_page_carries_the_same_bytes(structure, parsed_files, capabilities):
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    deterministic = GenerationConfig(deterministic=True)
    prose = GenerationConfig()
    args = dict(repo_name="testrepo", parsed_files=parsed_files, capabilities=capabilities)

    stub_page = await PageGenerator(
        MockProvider(), ContextAssembler(deterministic), deterministic
    ).generate_repo_overview(structure, {}, [], {}, **args)
    model_page = await PageGenerator(
        MockProvider(), ContextAssembler(prose), prose
    ).generate_repo_overview(structure, {}, [], {}, **args)

    table = build_capability_table(capabilities)
    assert table in stub_page.content
    assert table in model_page.content


async def test_a_provider_outage_costs_the_prose_not_the_table(
    structure, parsed_files, capabilities
):
    """The same reasoning as the architecture map: a fact the run already holds
    should survive the provider that was not needed to produce it."""
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    class Failing(MockProvider):
        async def generate(self, *a, **kw):
            raise RuntimeError("upstream 529 overloaded")

    config = GenerationConfig()
    gen = PageGenerator(Failing(), ContextAssembler(config), config)
    page = await gen.generate_repo_overview(
        structure,
        {},
        [],
        {},
        repo_name="testrepo",
        parsed_files=parsed_files,
        capabilities=capabilities,
    )

    from repowise.core.generation.models import STUB_FALLBACK_ERROR

    # The fallback path really was taken, rather than the assertion below
    # passing because the provider was never reached.
    assert "529 overloaded" in page.metadata[STUB_FALLBACK_ERROR]
    assert "| Capability | What it is | Where it is written |" in page.content


async def test_the_page_is_byte_identical_across_two_runs(structure, parsed_files, capabilities):
    """The point of building this outside the model."""
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    args = dict(repo_name="testrepo", parsed_files=parsed_files, capabilities=capabilities)
    first = await gen.generate_repo_overview(structure, {}, [], {}, **args)
    second = await gen.generate_repo_overview(structure, {}, [], {}, **args)
    assert first.content == second.content


async def test_the_table_only_adds_path_citations(structure, parsed_files, capabilities):
    """A page that reads better while citing less is a worse answer source.
    The third column is a real path, so this can only go up."""
    import re

    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    def cites(text: str) -> int:
        return len(re.findall(r"`[^`\n]*/[^`\n]*`", text))

    config = GenerationConfig(deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    args = dict(repo_name="testrepo", parsed_files=parsed_files)
    without = await gen.generate_repo_overview(structure, {}, [], {}, **args)
    with_table = await gen.generate_repo_overview(
        structure, {}, [], {}, capabilities=capabilities, **args
    )

    assert cites(with_table.content) > cites(without.content)


async def test_no_capabilities_leaves_the_page_as_it_was(structure, parsed_files):
    """A repository whose documents name nothing its code also spells is a
    supported and common outcome."""
    from repowise.core.generation.models import GenerationConfig
    from repowise.core.generation.page_generator import PageGenerator
    from repowise.core.providers.llm.mock import MockProvider

    config = GenerationConfig(deterministic=True)
    gen = PageGenerator(MockProvider(), ContextAssembler(config), config)
    page = await gen.generate_repo_overview(
        structure, {}, [], {}, repo_name="testrepo", parsed_files=parsed_files
    )
    assert CAPABILITY_TABLE_HEADING not in page.content
