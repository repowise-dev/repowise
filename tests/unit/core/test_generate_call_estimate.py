"""The call count an upgrade line is timed by is the one ``generate`` prices.

``generate`` writes the unwritten pages and then runs the decision lanes a
keyless index skipped. An estimate that counts only the pages promises half
the wait, so both sides read one count: ``generate_model_calls``.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import update

from repowise.cli.commands.decision_lanes import with_lane_plan
from repowise.cli.commands.generate_cmd.engine import run_scoped_generation
from repowise.cli.helpers import get_db_url_for_repo
from repowise.cli.ui.subscription import model_call_count
from repowise.core.decision_lanes import PENDING_DECISION_LANES_KEY
from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.page_selection import PageSelectionIntent
from repowise.core.generation.scope import generate_model_calls, unwritten_model_page_count
from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
    init_db,
    upsert_repository,
)
from repowise.core.persistence.crud import (
    batch_upsert_graph_nodes,
    upsert_git_metadata_bulk,
    upsert_page,
)
from repowise.core.persistence.models import Page

_COMMIT = {
    "sha": "9a0b27a7",
    "message": "perf: stop importing the workspace stack from the hook path",
    "subject": "perf: stop importing the workspace stack from the hook path",
    "body": (
        "## Why\n\nWe chose instead to resolve CLI command modules lazily, "
        "because the import cost was charged to every invocation."
    ),
    "pr_number": 2439,
    "author": "someone",
    "date": "2026-09-01",
}


def _write_repo(root: Path) -> None:
    (root / ".repowise").mkdir()
    (root / "pkg").mkdir()
    (root / "pkg" / "hooks.py").write_text(
        '"""Hook entry.\n\nCommands load lazily because the import cost was charged '
        'to every invocation, instead of once."""\n\n\ndef run():\n    return 1\n',
        encoding="utf-8",
    )
    state = {PENDING_DECISION_LANES_KEY: ["git_archaeology", "pr", "comment"]}
    (root / ".repowise" / "state.json").write_text(json.dumps(state), encoding="utf-8")


async def _seed(root: Path, *, graph: bool = True) -> str:
    engine = create_engine(get_db_url_for_repo(root))
    await init_db(engine)
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name=root.name, local_path=str(root))
            if graph:
                await batch_upsert_graph_nodes(
                    session,
                    repo.id,
                    [{"node_id": "pkg/hooks.py", "node_type": "file", "language": "python"}],
                )
            await upsert_git_metadata_bulk(
                session,
                repo.id,
                [{"file_path": "pkg/hooks.py", "significant_commits_json": json.dumps([_COMMIT])}],
            )
            # (id, type, target, provider, metadata): a structural stub, two
            # concept stubs, a model page with deterministic prose (still a
            # stub to the planner) and a written page the user pinned.
            pages = [
                ("file_page:pkg/hooks.py", "file_page", "pkg/hooks.py", "template", None),
                ("module_page:pkg", "module_page", "pkg", "template", None),
                ("repo_overview:" + root.name, "repo_overview", root.name, "template", None),
                ("module_page:det", "module_page", "det", "mock", {"deterministic": True}),
                ("module_page:pinned", "module_page", "pinned", "mock", None),
            ]
            for page_id, page_type, target, provider, metadata in pages:
                await upsert_page(
                    session,
                    page_id=page_id,
                    repository_id=repo.id,
                    page_type=page_type,
                    title=page_id,
                    content="# Page",
                    summary="Page",
                    target_path=target,
                    source_hash="h",
                    model_name=provider,
                    provider_name=provider,
                    metadata=metadata,
                )
            await session.execute(
                update(Page).where(Page.id == "module_page:pinned").values(pinned=True)
            )
            return repo.id
    finally:
        await engine.dispose()


async def _shared_count(root: Path, repo_id: str) -> tuple[int, int]:
    engine = create_engine(get_db_url_for_repo(root))
    try:
        async with get_session(create_session_factory(engine)) as session:
            pages = await unwritten_model_page_count(session, repo_id)
            return pages, await generate_model_calls(session, repo_id, root)
    finally:
        await engine.dispose()


async def _generate_count(root: Path) -> int:
    seen: dict = {}

    def gate_cost(cost_plans, provider, repo_path, *, yes, dry_run, decisions=None):
        seen["calls"] = model_call_count(with_lane_plan(cost_plans, decisions))
        return False

    await run_scoped_generation(
        root,
        None,
        GenerationConfig(),
        intent=PageSelectionIntent(unwritten=True),
        cascade_mode="none",
        exclude_patterns=[],
        embedder_name=None,
        yes=True,
        dry_run=True,
        gate_cost=gate_cost,
    )
    return seen["calls"]


async def test_the_upgrade_line_counts_the_calls_generate_prices(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    repo_id = await _seed(tmp_path)

    pages, shared = await _shared_count(tmp_path, repo_id)
    priced = await _generate_count(tmp_path)

    # Three unwritten concept pages; generate also rewrites the pinned one,
    # and the pending lanes add their calls on top.
    assert pages == 3
    assert shared > pages + 1
    assert shared == priced


async def test_no_pending_lanes_counts_only_the_pages(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    repo_id = await _seed(tmp_path)

    pages, shared = await _shared_count(tmp_path, repo_id)

    assert pages == 3
    assert shared == pages + 1
    assert await _generate_count(tmp_path) == shared


async def test_a_missing_graph_counts_the_pages_without_failing(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    repo_id = await _seed(tmp_path, graph=False)

    pages, shared = await _shared_count(tmp_path, repo_id)

    assert shared == pages + 1
