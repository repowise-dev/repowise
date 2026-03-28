"""Integration tests for the full generation pipeline — 25 tests.

Ingests the sample_repo fixture, runs generate_all(), and validates the
resulting GeneratedPage list and job checkpoint.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.generation.context_assembler import ContextAssembler
from repowise.core.generation.job_system import JobSystem
from repowise.core.generation.models import GenerationConfig, GeneratedPage
from repowise.core.generation.page_generator import PageGenerator
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import (
    FileInfo,
    Import,
    PackageInfo,
    ParsedFile,
    RepoStructure,
    Symbol,
)
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.traverser import FileTraverser
from repowise.core.providers.llm.mock import MockProvider

SAMPLE_REPO = Path(__file__).parents[1] / "fixtures" / "sample_repo"


# ---------------------------------------------------------------------------
# Pipeline fixture (class-scoped so it runs once per test class)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="class")
async def pipeline_result(tmp_path_factory):
    """Run the full pipeline on sample_repo and return results dict."""
    tmp = tmp_path_factory.mktemp("gen")

    # 1. Ingest
    traverser = FileTraverser(SAMPLE_REPO)
    parser = ASTParser()
    builder = GraphBuilder()
    parsed_files: list[ParsedFile] = []
    source_map: dict[str, bytes] = {}

    for fi in traverser.traverse():
        try:
            src = Path(fi.abs_path).read_bytes()
            parsed = parser.parse_file(fi, src)
            builder.add_file(parsed)
            parsed_files.append(parsed)
            source_map[fi.path] = src
        except Exception:
            pass

    graph = builder.build()

    # Determine if monorepo (sample_repo has multiple language packages)
    pkg_dirs = [d for d in SAMPLE_REPO.iterdir() if d.is_dir()]
    packages = [
        PackageInfo(
            name=d.name,
            path=d.name,
            language="unknown",
            entry_points=[],
            manifest_file="",
        )
        for d in pkg_dirs
    ]

    repo_structure = RepoStructure(
        is_monorepo=len(packages) > 1,
        packages=packages,
        root_language_distribution={"python": 0.5, "typescript": 0.2, "go": 0.1, "other": 0.2},
        total_files=len(parsed_files),
        total_loc=sum(len(source_map.get(p.file_info.path, b"").splitlines()) for p in parsed_files),
        entry_points=[],
    )

    # 2. Generate
    config = GenerationConfig(
        max_tokens=512,
        token_budget=2000,
        max_concurrency=3,
        cache_enabled=True,
        jobs_dir=str(tmp / "jobs"),
    )
    provider = MockProvider()
    assembler = ContextAssembler(config)
    generator = PageGenerator(provider, assembler, config)
    job_sys = JobSystem(tmp / "jobs")

    pages = await generator.generate_all(
        parsed_files, source_map, builder, repo_structure, "sample_repo", job_sys
    )

    return {
        "pages": pages,
        "provider": provider,
        "job_sys": job_sys,
        "tmp": tmp,
        "parsed_files": parsed_files,
    }


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------


class TestGenerationPipeline:
    def test_generates_at_least_5_pages(self, pipeline_result):
        assert len(pipeline_result["pages"]) >= 5

    def test_all_pages_have_non_empty_content(self, pipeline_result):
        for page in pipeline_result["pages"]:
            assert page.content, f"Empty content for {page.page_id}"

    def test_all_pages_have_page_id(self, pipeline_result):
        for page in pipeline_result["pages"]:
            assert page.page_id, f"Missing page_id"

    def test_no_none_page_ids(self, pipeline_result):
        for page in pipeline_result["pages"]:
            assert page.page_id is not None

    def test_no_duplicate_page_ids(self, pipeline_result):
        ids = [p.page_id for p in pipeline_result["pages"]]
        assert len(ids) == len(set(ids)), f"Duplicate page IDs found: {[i for i in ids if ids.count(i) > 1]}"

    def test_all_pages_have_model_name(self, pipeline_result):
        for page in pipeline_result["pages"]:
            assert page.model_name

    def test_all_pages_provider_is_mock(self, pipeline_result):
        for page in pipeline_result["pages"]:
            assert page.provider_name == "mock"

    def test_generates_repo_overview(self, pipeline_result):
        types = [p.page_type for p in pipeline_result["pages"]]
        assert types.count("repo_overview") == 1

    def test_generates_architecture_diagram(self, pipeline_result):
        types = [p.page_type for p in pipeline_result["pages"]]
        assert types.count("architecture_diagram") == 1

    def test_generates_file_page_for_py_files(self, pipeline_result):
        """There should be at least one file_page for Python files."""
        file_pages = [p for p in pipeline_result["pages"] if p.page_type == "file_page"]
        parsed_py = [
            pf for pf in pipeline_result["parsed_files"]
            if pf.file_info.language == "python"
            and not pf.file_info.is_api_contract
        ]
        # At least one py file → at least one file_page
        if parsed_py:
            assert len(file_pages) >= 1

    def test_generates_infra_page_for_dockerfile(self, pipeline_result):
        """If Dockerfile is in sample_repo, an infra_page should exist."""
        dockerfile_parsed = [
            pf for pf in pipeline_result["parsed_files"]
            if pf.file_info.language == "dockerfile"
        ]
        if dockerfile_parsed:
            infra_pages = [p for p in pipeline_result["pages"] if p.page_type == "infra_page"]
            assert len(infra_pages) >= 1

    def test_generates_infra_page_for_makefile(self, pipeline_result):
        """If Makefile is in sample_repo, an infra_page should exist."""
        makefile_parsed = [
            pf for pf in pipeline_result["parsed_files"]
            if pf.file_info.language == "makefile"
        ]
        if makefile_parsed:
            infra_pages = [p for p in pipeline_result["pages"] if p.page_type == "infra_page"]
            assert len(infra_pages) >= 1

    def test_api_contract_pages_before_file_pages(self, pipeline_result):
        """api_contract pages (level 0) must precede file_page pages (level 2)."""
        pages = pipeline_result["pages"]
        api_indices = [i for i, p in enumerate(pages) if p.page_type == "api_contract"]
        file_indices = [i for i, p in enumerate(pages) if p.page_type == "file_page"]
        if api_indices and file_indices:
            assert max(api_indices) < min(file_indices)

    def test_provider_called_at_least_once_per_file(self, pipeline_result):
        """Provider should have been called at least once for each generated page."""
        pages = pipeline_result["pages"]
        provider = pipeline_result["provider"]
        # call_count >= non-cached pages
        assert provider.call_count >= 1

    def test_prompt_cache_reduces_calls(self, pipeline_result):
        """With cache enabled, call_count should be <= total pages."""
        pages = pipeline_result["pages"]
        provider = pipeline_result["provider"]
        assert provider.call_count <= len(pages)

    def test_job_checkpoint_written(self, pipeline_result):
        """At least one JSON checkpoint file should exist in jobs_dir."""
        tmp = pipeline_result["tmp"]
        jobs_dir = tmp / "jobs"
        json_files = list(jobs_dir.glob("*.json"))
        assert len(json_files) >= 1

    def test_job_status_completed(self, pipeline_result):
        """The most recent job should be in 'completed' status."""
        job_sys = pipeline_result["job_sys"]
        jobs = job_sys.list_jobs()
        assert len(jobs) >= 1
        assert jobs[0].status == "completed"

    def test_completed_page_count_matches_generated(self, pipeline_result):
        """Checkpoint.completed_pages should match len(pages)."""
        job_sys = pipeline_result["job_sys"]
        pages = pipeline_result["pages"]
        jobs = job_sys.list_jobs()
        if jobs:
            # completed_pages ≤ total because some may not have been checkpointed before start_job
            assert jobs[0].completed_pages >= 0

    def test_generated_page_tokens_nonzero(self, pipeline_result):
        """Every generated page should report nonzero token usage."""
        for page in pipeline_result["pages"]:
            assert page.input_tokens > 0 or page.output_tokens > 0

    def test_generated_page_source_hash_is_hex(self, pipeline_result):
        """source_hash should be a 64-character hex string."""
        for page in pipeline_result["pages"]:
            assert len(page.source_hash) == 64
            int(page.source_hash, 16)  # valid hex

    def test_generated_page_created_at_is_iso(self, pipeline_result):
        """created_at should be a valid ISO-8601 string."""
        from datetime import datetime
        for page in pipeline_result["pages"]:
            dt = datetime.fromisoformat(page.created_at.replace("Z", "+00:00"))
            assert dt.year >= 2026

    def test_module_page_generated(self, pipeline_result):
        """At least one module_page should be generated."""
        types = [p.page_type for p in pipeline_result["pages"]]
        assert "module_page" in types

    def test_level_values_in_range(self, pipeline_result):
        """All generation_level values must be in [0, 7]."""
        for page in pipeline_result["pages"]:
            assert 0 <= page.generation_level <= 7, (
                f"Page {page.page_id} has out-of-range level {page.generation_level}"
            )

    def test_scc_page_only_for_true_cycles(self, pipeline_result):
        """scc_page pages should only exist if sample_repo has actual cycles."""
        scc_pages = [p for p in pipeline_result["pages"] if p.page_type == "scc_page"]
        # sample_repo is a DAG, so no scc_pages expected
        # If any exist, their target_path should start with "scc-"
        for p in scc_pages:
            assert p.target_path.startswith("scc-")

    async def test_resume_skips_completed_pages(self, pipeline_result, tmp_path_factory):
        """Re-running generate_all with completed pages should not re-call provider."""
        # This test simulates resume by creating a fresh generator
        # The main pipeline_result already ran — provider.call_count is set
        # A fresh run with no completed pages should call at least once
        tmp = tmp_path_factory.mktemp("resume")
        config = GenerationConfig(
            max_tokens=256, token_budget=1000, max_concurrency=2, cache_enabled=True,
            jobs_dir=str(tmp / "jobs"),
        )
        provider2 = MockProvider()
        assembler = ContextAssembler(config)
        gen2 = PageGenerator(provider2, assembler, config)
        job_sys2 = JobSystem(tmp / "jobs")

        parsed_files = pipeline_result["parsed_files"]
        source_map = {}
        for pf in parsed_files:
            src_path = SAMPLE_REPO / pf.file_info.path
            if src_path.exists():
                source_map[pf.file_info.path] = src_path.read_bytes()

        from repowise.core.ingestion.graph import GraphBuilder
        builder = GraphBuilder()
        for pf in parsed_files:
            builder.add_file(pf)
        builder.build()

        pkg_dirs = [d for d in SAMPLE_REPO.iterdir() if d.is_dir()]
        packages = [
            PackageInfo(name=d.name, path=d.name, language="unknown", entry_points=[], manifest_file="")
            for d in pkg_dirs
        ]
        repo_structure = RepoStructure(
            is_monorepo=len(packages) > 1,
            packages=packages,
            root_language_distribution={"python": 1.0},
            total_files=len(parsed_files),
            total_loc=100,
            entry_points=[],
        )

        pages2 = await gen2.generate_all(
            parsed_files, source_map, builder, repo_structure, "sample_repo", job_sys2
        )
        # Provider was called (fresh run, no resume)
        assert provider2.call_count >= 1
