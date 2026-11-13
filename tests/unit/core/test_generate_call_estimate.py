"""The call count an upgrade line is timed by is the one ``generate`` prices.

``generate`` writes the unwritten pages and then runs the decision lanes a
keyless index skipped. An estimate that counts only the pages promises half
the wait, so both sides read one count: ``generate_model_calls``.
"""

from __future__ import annotations

import json
from pathlib import Path

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


async def _seed(root: Path) -> str:
    engine = create_engine(get_db_url_for_repo(root))
    await init_db(engine)
    try:
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name=root.name, local_path=str(root))
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
            pages = [
                ("file_page:pkg/hooks.py", "file_page", "pkg/hooks.py"),
                ("module_page:pkg", "module_page", "pkg"),
                ("repo_overview:" + root.name, "repo_overview", root.name),
            ]
            for page_id, page_type, target in pages:
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
                    model_name="template",
                    provider_name="template",
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

    # The pending lanes add calls on top of the two unwritten concept pages.
    assert pages == 2
    assert shared > pages
    assert shared == priced


async def test_no_pending_lanes_counts_only_the_pages(tmp_path: Path) -> None:
    _write_repo(tmp_path)
    (tmp_path / ".repowise" / "state.json").write_text("{}", encoding="utf-8")
    repo_id = await _seed(tmp_path)

    pages, shared = await _shared_count(tmp_path, repo_id)

    assert shared == pages == 2
    assert await _generate_count(tmp_path) == shared
