"""What a generating run claims to have decided, and therefore what the sweep may retire.

``authoritative_page_types`` is the signal ``_sweep_stale_generated_pages``
reads before deleting prior rows of a page type the run did not reproduce. Get
it wrong in the permissive direction and a degraded run wipes good pages; get
it wrong in the strict direction and superseded pages accumulate forever.

``module_page`` is the interesting one. The concept grouping partitions every
production file, takes no optional input, and is not rationed by the coverage
budget, so a run that produced any concept page produced all of them. It used
to be declared authoritative only when a curated knowledge-graph artifact was
present, which left exactly the KG-less run, the one carrying the most stale
rows, unable to clean up after itself.

The claim is still made on evidence rather than on "generation ran", because
there are ways to emit zero concept pages that do not mean "there is nothing
to document" — ``file_pages_only`` returns before that level, and a provider
error on it is logged per page rather than raised.
"""

from __future__ import annotations

from repowise.core.pipeline import run_pipeline
from repowise.core.providers.llm.mock import MockProvider


def _write_repo(root):
    root.mkdir()
    (root / "pkg").mkdir()
    (root / "pkg" / "main.py").write_text(
        "def hello() -> str:\n    return 'hello'\n",
        encoding="utf-8",
    )
    (root / "pkg" / "util.py").write_text(
        "def helper(n: int) -> int:\n    return n + 1\n",
        encoding="utf-8",
    )
    return root


async def test_generating_run_is_authoritative_for_module_pages(tmp_path):
    repo_path = _write_repo(tmp_path / "repo")

    result = await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=MockProvider(),
        concurrency=1,
        test_run=True,
    )

    assert result.generated_pages
    assert "module_page" in result.authoritative_page_types


async def test_a_run_that_does_not_generate_claims_nothing(tmp_path):
    """No pages produced means no page type was decided.

    An index-only run has no opinion about which concept pages should exist,
    so letting it claim authority would let it retire every one of them.
    """
    repo_path = _write_repo(tmp_path / "repo")

    result = await run_pipeline(
        repo_path,
        generate_docs=False,
        concurrency=1,
        test_run=True,
    )

    assert not result.authoritative_page_types


async def test_a_run_that_never_reaches_the_concept_level_claims_nothing(tmp_path):
    """``file_pages_only`` stops before concept pages exist.

    Claiming authority there would let a file-pages-only run delete every
    concept page in the store, which is the most destructive shape this
    signal has.
    """
    from repowise.core.generation.models import GenerationConfig

    repo_path = _write_repo(tmp_path / "repo")

    result = await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=MockProvider(),
        concurrency=1,
        test_run=True,
        generation_config=GenerationConfig(file_pages_only=True),
    )

    assert result.generated_pages
    assert not any(p.page_type == "module_page" for p in result.generated_pages)
    assert "module_page" not in result.authoritative_page_types


async def test_a_repository_with_no_production_code_still_claims(tmp_path):
    """Zero pages because there is nothing to group is the case the sweep wants.

    A repository whose code was deleted, or that is only tests, should have
    its concept pages retired rather than kept forever behind an
    "it produced nothing, so trust nothing" rule.
    """
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "tests").mkdir()
    (repo_path / "tests" / "test_thing.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )

    result = await run_pipeline(
        repo_path,
        generate_docs=True,
        llm_client=MockProvider(),
        concurrency=1,
        test_run=True,
    )

    assert not any(p.page_type == "module_page" for p in (result.generated_pages or []))
    assert "module_page" in result.authoritative_page_types
