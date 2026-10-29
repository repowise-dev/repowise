"""Which structure-only pages exist and what they claim: classifiers, the
sweeps that retire what they stop picking, the pages an incremental run renders
by id, and the targets an infra page lists."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from repowise.core.generation.models import compute_page_id, scc_page_slug
from repowise.core.generation.page_generator.helpers import _is_infra_file
from repowise.core.generation.selection.selector import (
    selected_file_keyed_paths,
    structure_ids_for_incremental_run,
)
from repowise.core.persistence.models import Page
from repowise.core.pipeline.persist import (
    TOMBSTONE_RETENTION,
    clear_template_model_names,
    mark_tombstone_pages,
    sweep_deselected_file_keyed_pages,
    sweep_expired_tombstones,
)
from tests.unit.persistence.helpers import insert_repo, make_page_kwargs


@dataclass
class _FileInfo:
    path: str
    language: str
    size_bytes: int = 2_000
    is_test: bool = False
    is_api_contract: bool = False


@dataclass
class _Parsed:
    file_info: _FileInfo
    symbols: list = field(default_factory=list)


def _repo_files() -> list[_Parsed]:
    return [
        _Parsed(_FileInfo("docker/Dockerfile", "dockerfile")),
        _Parsed(_FileInfo("Makefile", "makefile")),
        _Parsed(_FileInfo("docker/entrypoint.sh", "shell")),
        _Parsed(_FileInfo("scripts/lint.sh", "shell")),
        _Parsed(_FileInfo("tests/e2e/reset.sh", "shell", is_test=True)),
        _Parsed(_FileInfo("api/openapi.yaml", "openapi", is_api_contract=True)),
        _Parsed(_FileInfo("tests/test_openapi.py", "python", is_test=True)),
        _Parsed(_FileInfo("src/extractors/openapi.py", "python")),
    ]


class TestClassifiers:
    def test_lint_and_test_scripts_are_not_infrastructure(self):
        by_path = {p.file_info.path: _is_infra_file(p) for p in _repo_files()}
        assert by_path["docker/Dockerfile"]
        assert by_path["Makefile"]
        assert by_path["docker/entrypoint.sh"]
        assert not by_path["scripts/lint.sh"]
        assert not by_path["tests/e2e/reset.sh"]

    def test_only_api_surfaces_are_api_contracts(self):
        selected = selected_file_keyed_paths(_repo_files())
        assert selected["api_contract"] == {"api/openapi.yaml"}
        assert selected["infra_page"] == {"docker/Dockerfile", "Makefile", "docker/entrypoint.sh"}


class TestInfraTargets:
    def _targets(self, path: str, language: str, source: bytes) -> list[str]:
        from repowise.core.generation.context_assembler import ContextAssembler
        from repowise.core.generation.models import GenerationConfig
        from repowise.core.ingestion.models import FileInfo
        from repowise.core.ingestion.parser import ASTParser

        info = FileInfo(
            path=path,
            abs_path=f"/repo/{path}",
            language=language,  # type: ignore[arg-type]
            size_bytes=len(source),
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=True,
            is_api_contract=False,
            is_entry_point=False,
        )
        parsed = ASTParser().parse_file(info, source)
        return ContextAssembler(GenerationConfig()).assemble_infra_page(parsed, source).targets

    def test_a_dockerfile_lists_its_build_stages_not_its_instructions(self):
        source = (
            b"FROM node:20 AS frontend-builder\nRUN npm ci\n"
            b"FROM --platform=linux/amd64 python:3.12-slim AS runtime\n"
            b'EXPOSE 7337 3000\nENTRYPOINT ["/app/entrypoint.sh"]\n'
        )
        assert self._targets("docker/Dockerfile", "dockerfile", source) == [
            "frontend-builder",
            "runtime",
        ]

    def test_a_makefile_lists_its_targets(self):
        assert self._targets("Makefile", "makefile", b"build: deps\n\tgo build\n") == ["build"]

    def test_a_script_declares_no_targets(self):
        source = b"#!/bin/sh\nmain() { exec app; }\nmain\n"
        assert self._targets("docker/entrypoint.sh", "shell", source) == []


class TestIncrementalStructureIds:
    CYCLE = frozenset({"src/a.py", "src/b.py"})

    def _ids(self, existing=(), rendered=()):
        return structure_ids_for_incremental_run(_repo_files(), [self.CYCLE], existing, rendered)

    def test_a_new_cycle_gets_a_page(self):
        cycle_id = compute_page_id("scc_page", scc_page_slug(sorted(self.CYCLE)))
        assert cycle_id in self._ids()
        assert cycle_id not in self._ids(existing=[cycle_id])

    def test_a_changed_infra_file_is_rendered_even_when_its_page_exists(self):
        page_id = compute_page_id("infra_page", "docker/Dockerfile")
        assert page_id not in self._ids(existing=[page_id])
        assert page_id in self._ids(existing=[page_id], rendered=["docker/Dockerfile"])

    def test_a_changed_api_contract_is_left_to_the_file_scoped_pass(self):
        page_id = compute_page_id("api_contract", "api/openapi.yaml")
        assert page_id in self._ids()
        assert page_id not in self._ids(rendered=["api/openapi.yaml"])


@pytest.fixture
async def repo_id(async_session) -> str:
    return (await insert_repo(async_session)).id


async def _add(async_session, repo_id: str, page_type: str, target: str, **kw) -> str:
    from repowise.core.persistence.crud import upsert_page

    page_id = compute_page_id(page_type, target)
    await upsert_page(
        async_session,
        **make_page_kwargs(
            repo_id, page_id=page_id, page_type=page_type, target_path=target, **kw
        ),
    )
    await async_session.flush()
    return page_id


async def _ids(async_session) -> set[str]:
    return {r.id for r in (await async_session.execute(Page.__table__.select())).fetchall()}


@pytest.mark.asyncio
class TestDeselectedFileKeyedPages:
    async def test_misclassified_pages_are_retired(self, async_session, repo_id):
        test_api = await _add(async_session, repo_id, "api_contract", "tests/test_openapi.py")
        parser_api = await _add(async_session, repo_id, "api_contract", "src/extractors/openapi.py")
        lint = await _add(async_session, repo_id, "infra_page", "scripts/lint.sh")
        kept = {
            await _add(async_session, repo_id, "api_contract", "api/openapi.yaml"),
            await _add(async_session, repo_id, "infra_page", "docker/Dockerfile"),
            # Not in this run's parse, so not judged.
            await _add(async_session, repo_id, "infra_page", "deploy/other.sh"),
        }

        swept = await sweep_deselected_file_keyed_pages(async_session, repo_id, _repo_files())

        assert sorted(swept) == sorted([test_api, parser_api, lint])
        assert await _ids(async_session) == kept

    async def test_no_parse_judges_nothing(self, async_session, repo_id):
        page = await _add(async_session, repo_id, "infra_page", "scripts/lint.sh")
        assert await sweep_deselected_file_keyed_pages(async_session, repo_id, []) == []
        assert await _ids(async_session) == {page}


@pytest.mark.asyncio
class TestTemplateModelNames:
    async def test_a_template_page_credits_no_model(self, async_session, repo_id):
        template = await _add(
            async_session, repo_id, "infra_page", "Makefile",
            provider_name="template", model_name="gpt-old",
        )
        written = await _add(
            async_session, repo_id, "module_page", "src",
            provider_name="openai", model_name="gpt-old",
        )

        assert await clear_template_model_names(async_session, repo_id) == 1

        assert (await async_session.get(Page, template)).model_name == ""
        assert (await async_session.get(Page, written)).model_name == "gpt-old"


@pytest.mark.asyncio
class TestExpiredTombstones:
    async def _tombstone(self, async_session, repo_id, path, successors=(), age_days=0):
        page_id = await _add(async_session, repo_id, "file_page", path)
        page = await async_session.get(Page, page_id)
        page.freshness_status = "tombstone"
        stamped = datetime.now(UTC) - timedelta(days=age_days)
        page.metadata_json = json.dumps(
            {"successor_paths": list(successors), "tombstoned_at": stamped.isoformat()}
        )
        await async_session.flush()
        return page_id

    async def test_only_old_deletions_expire(self, async_session, repo_id):
        days = TOMBSTONE_RETENTION.days
        old = await self._tombstone(async_session, repo_id, "gone.py", age_days=days + 1)
        recent = await self._tombstone(async_session, repo_id, "new_gone.py", age_days=1)
        renamed = await self._tombstone(
            async_session, repo_id, "moved.py", ["moved_to.py"], age_days=days + 1
        )

        assert await sweep_expired_tombstones(async_session, repo_id) == [old]
        assert await _ids(async_session) == {recent, renamed}

    async def test_marking_a_tombstone_stamps_when(self, async_session, repo_id):
        page_id = await _add(async_session, repo_id, "file_page", "src/deleted.py")

        await mark_tombstone_pages(async_session, repo_id, [("src/deleted.py", [])])

        meta = json.loads((await async_session.get(Page, page_id)).metadata_json)
        assert datetime.fromisoformat(meta["tombstoned_at"]) > datetime.now(UTC) - timedelta(
            minutes=5
        )
        # Just stamped, so not expired.
        assert await sweep_expired_tombstones(async_session, repo_id) == []
