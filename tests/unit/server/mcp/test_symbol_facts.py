"""The fact row and direct test links on ``get_symbol`` and the ``get_context`` symbol card.

Every statement is positive: an empty or unmapped store adds nothing, callers
are counted over the resolution origins that measured clean, tests one hop out
are listed as they are, two hops out only with the function in between, and
three hops out never.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from repowise.core.persistence.models import (
    FunctionFact,
    GraphEdge,
    GraphNode,
    HealthFileMetric,
    Repository,
    WikiSymbol,
)
from repowise.server.mcp_server._symbol_facts import shows_call_origin, symbol_card_facts

_NOW = datetime(2026, 3, 19, 12, 0, 0, tzinfo=UTC)

TARGET = "pkg/work.py::run"
HELPER = "pkg/work.py::prepare"
PLAIN = "pkg/work.py::plain"
DIRECT_TEST = "tests/test_work.py::test_run"
CHAIN_TEST = "tests/test_work.py::test_prepare"
FAR_TEST = "tests/test_far.py::test_far"
LOST_TEST = "tests/test_lost.py::test_lost"

_SOURCE = (
    "async def run(self):\n"
    "    self.count = 1\n"
    "    self.name = 'x'\n"
    "    await other()\n"
    "\n"
    "def prepare():\n"
    "    return run(None)\n"
    "\n"
    "def plain():\n"
    "    return 1\n"
)


async def _repo(session) -> Repository:
    return (await session.execute(select(Repository))).scalars().first()


async def _seed(session, tmp_path) -> str:
    repo = await _repo(session)
    (tmp_path / "pkg").mkdir(exist_ok=True)
    (tmp_path / "pkg" / "work.py").write_text(_SOURCE)
    nodes = [
        TARGET,
        HELPER,
        PLAIN,
        "pkg/api.py::handler",
        "pkg/merged.py::guess",
        "pkg/unique.py::guess",
        "pkg/old.py::unlabelled",
        "pkg/marked.py::check",
        DIRECT_TEST,
        CHAIN_TEST,
        FAR_TEST,
        LOST_TEST,
    ]
    for node_id in nodes:
        session.add(
            GraphNode(
                id=f"f_{node_id}",
                repository_id=repo.id,
                node_id=node_id,
                node_type="symbol",
                name=node_id.split("::")[-1],
                file_path=node_id.split("::")[0],
                kind="function",
                language="python",
                start_line=1,
                end_line=4,
                created_at=_NOW,
            )
        )

    def edge(source: str, target: str, origin: str | None, confidence: float = 0.9) -> None:
        session.add(
            GraphEdge(
                id=f"f_{source}_{target}",
                repository_id=repo.id,
                source_node_id=source,
                target_node_id=target,
                edge_type="calls",
                confidence=confidence,
                resolution_origin=origin,
                created_at=_NOW,
            )
        )

    edge(HELPER, TARGET, "same_file", 0.95)
    edge("pkg/api.py::handler", TARGET, "import_scoped")
    edge("pkg/marked.py::check", TARGET, "import_scoped")
    edge(DIRECT_TEST, TARGET, "import_scoped")
    # Weaker origins and unlabelled rows are never counted.
    edge("pkg/merged.py::guess", TARGET, "import_merged", 0.85)
    edge("pkg/unique.py::guess", TARGET, "global_unique", 0.5)
    edge("pkg/old.py::unlabelled", TARGET, None)
    edge(CHAIN_TEST, HELPER, "import_scoped")
    # The stored origin decides, not the path: this file's code is test code.
    session.add(
        HealthFileMetric(
            repository_id=repo.id, file_path="pkg/marked.py", code_origin="test", is_test=False
        )
    )
    session.add(
        FunctionFact(
            repository_id=repo.id,
            symbol_id=TARGET,
            awaits=True,
            is_generator=False,
            uses_receiver=True,
            receiver_assigns_known=True,
            receiver_assigns_json=json.dumps(["name", "count"]),
            early_exits=0,
            ccn=3,
            tests_json=json.dumps(
                [[DIRECT_TEST, 1], [CHAIN_TEST, 2], [LOST_TEST, 2], [FAR_TEST, 3]]
            ),
        )
    )
    # Mapped with no test reached, and never mapped: neither says anything.
    session.add(
        FunctionFact(
            repository_id=repo.id,
            symbol_id=PLAIN,
            awaits=False,
            is_generator=False,
            receiver_assigns_known=True,
            receiver_assigns_json=None,
            tests_json="[]",
        )
    )
    for sid, name, start, end in ((TARGET, "run", 1, 4), (PLAIN, "plain", 9, 10)):
        session.add(
            WikiSymbol(
                repository_id=repo.id,
                file_path="pkg/work.py",
                symbol_id=sid,
                name=name,
                qualified_name=f"pkg.work.{name}",
                kind="function",
                signature=f"def {name}()",
                start_line=start,
                end_line=end,
                language="python",
            )
        )
    await session.flush()
    return repo.id


@pytest.mark.asyncio
async def test_fact_row_and_test_links(setup_mcp, session, tmp_path):
    repo_id = await _seed(session, tmp_path)

    card = await symbol_card_facts(session, repo_id, TARGET)

    assert card["facts"] == (
        "awaits; assigns fields count, name; complexity 3; 4 resolved callers, 2 in tests"
    )
    assert card["tests"] == [
        {"run": DIRECT_TEST},
        # The function in between sits in the symbol's own file: named alone.
        {"run": CHAIN_TEST, "via": "prepare"},
    ]
    # Two hops with no chain found, and three hops, are left out.
    runs = json.dumps(card)
    assert LOST_TEST not in runs and FAR_TEST not in runs


@pytest.mark.asyncio
async def test_empty_stores_state_nothing(setup_mcp, session, tmp_path):
    repo_id = await _seed(session, tmp_path)

    assert await symbol_card_facts(session, repo_id, PLAIN) == {}
    assert await symbol_card_facts(session, repo_id, "pkg/nowhere.py::ghost") == {}


@pytest.mark.asyncio
async def test_get_symbol_carries_the_row_after_the_signature(setup_mcp, session, tmp_path):
    from repowise.server.mcp_server import get_symbol

    await _seed(session, tmp_path)

    result = await get_symbol(TARGET)
    keys = list(result)
    assert keys.index("signature") < keys.index("facts") < keys.index("tests")
    assert keys.index("tests") < keys.index("source")
    assert result["facts"].startswith("awaits;")

    plain = await get_symbol(PLAIN)
    assert "facts" not in plain and "tests" not in plain


@pytest.mark.asyncio
async def test_get_context_symbol_card_carries_the_row(setup_mcp, session, tmp_path):
    from repowise.server.mcp_server import get_context

    await _seed(session, tmp_path)

    result = await get_context(targets=[TARGET])
    docs = result["targets"][TARGET]["docs"]
    assert docs["facts"].endswith("4 resolved callers, 2 in tests")
    assert docs["tests"][0] == {"run": DIRECT_TEST}


def test_global_unique_is_hidden_for_typescript_only():
    assert not shows_call_origin("global_unique", "web/src/app.tsx")
    assert not shows_call_origin("global_unique", "web/src/util.js")
    assert shows_call_origin("global_unique", "pkg/work.py")
    assert shows_call_origin("import_scoped", "web/src/app.tsx")
    assert shows_call_origin(None, "web/src/app.tsx")


@pytest.mark.asyncio
async def test_typescript_used_by_drops_global_unique_users(setup_mcp, session):
    from repowise.server.mcp_server import get_context

    repo = await _repo(session)
    target = "web/src/icons.ts::Copy"
    for node_id in (target, "web/src/a.tsx::A", "web/src/b.tsx::B"):
        session.add(
            GraphNode(
                id=f"ts_{node_id}",
                repository_id=repo.id,
                node_id=node_id,
                node_type="symbol",
                name=node_id.split("::")[-1],
                file_path=node_id.split("::")[0],
                kind="function",
                language="typescript",
                start_line=1,
                end_line=3,
                created_at=_NOW,
            )
        )
    for source, origin in (("web/src/a.tsx::A", "import_scoped"), ("web/src/b.tsx::B", "global_unique")):
        session.add(
            GraphEdge(
                id=f"ts_{source}",
                repository_id=repo.id,
                source_node_id=source,
                target_node_id=target,
                edge_type="calls",
                confidence=0.9 if origin == "import_scoped" else 0.5,
                resolution_origin=origin,
                created_at=_NOW,
            )
        )
    await session.flush()

    docs = (await get_context(targets=[target]))["targets"][target]["docs"]
    assert docs["used_by"] == ["web/src/a.tsx"]
