"""The cross-repo ``tests`` block the MCP tools attach to a consumer row.

The helper is exercised against a real consumer index, because the two things
it has to get right are query-path facts: the repo is opened from the workspace
config by alias, and the block is keyed by the consumer file the link names.
The four states each have to reach the block intact, and a link the join could
not follow has to arrive as its reason, not as an empty list.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from repowise.core.analysis.test_reachability import MAX_TESTS_PER_TARGET
from repowise.core.ingestion.models import Symbol
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.contracts import ContractLink
from repowise.server.mcp_server import _state
from repowise.server.mcp_server._test_impact import (
    _TESTS_PER_CONSUMER_LIMIT,
    close_test_impact_indexes,
    cross_repo_tests,
)
from repowise.server.mcp_server._test_impact import (
    tests_block_for as _tests_block_for,
)

from ...unit.workspace._repo_index import make_repo_index

PROVIDER_FILE = "app/routers/users.py"
CONTRACT_ID = "http::GET::/users/{param}"


def _symbol(symbol_id: str) -> Symbol:
    name = symbol_id.split("::", 1)[1]
    return Symbol(
        id=symbol_id,
        name=name,
        qualified_name=name,
        kind="function",
        signature=f"function {name}()",
        start_line=1,
        end_line=5,
        docstring=None,
        visibility="public",
    )


def _link(
    *,
    consumer_file: str,
    consumer_symbol_id: str | None,
    consumer_repo: str = "frontend",
    contract_id: str = CONTRACT_ID,
) -> ContractLink:
    return ContractLink(
        contract_id=contract_id,
        contract_type="http",
        match_type="exact",
        confidence=0.9,
        provider_repo="backend",
        provider_file=PROVIDER_FILE,
        provider_symbol="get_user",
        provider_service=None,
        consumer_repo=consumer_repo,
        consumer_file=consumer_file,
        consumer_symbol="getUser",
        consumer_service=None,
        consumer_symbol_id=consumer_symbol_id,
    )


BOUND = _link(consumer_file="src/api.ts", consumer_symbol_id="src/api.ts::getUser")
UNBOUND = _link(consumer_file="src/legacy.ts", consumer_symbol_id=None)
NOTHING_REACHES = _link(
    consumer_file="src/other.ts", consumer_symbol_id="src/other.ts::helper"
)
ONLY_IMPORTED = _link(
    consumer_file="src/imported.ts", consumer_symbol_id="src/imported.ts::thing"
)
WIDE = _link(consumer_file="src/wide.ts", consumer_symbol_id="src/wide.ts::wide")
HUGE = _link(consumer_file="src/huge.ts", consumer_symbol_id="src/huge.ts::huge")

_WIDE_TESTS = _TESTS_PER_CONSUMER_LIMIT + 3

#: Above the per-pair cap the core join applies by default, so a total of
#: exactly this many proves the helper turned that cap off.
_HUGE_TESTS = MAX_TESTS_PER_TARGET + 10


class _FakeEnricher:
    """Stands in for the loaded contract artifact, indexed the same way."""

    has_contract_data = True

    def __init__(self, links: list[ContractLink]) -> None:
        # One dict per link, built once: the helper deduplicates by identity.
        self._by_provider: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for link in links:
            key = (link.provider_repo, link.provider_file)
            self._by_provider.setdefault(key, []).append(link.to_dict())

    def get_contract_links_as_provider(self, alias: str, path: str) -> list[dict[str, Any]]:
        return self._by_provider.get((alias, path), [])


class _FakeCollector:
    """Records what would have gone to the omission store."""

    def __init__(self) -> None:
        self.chunks: list[tuple[str, Any]] = []

    def add(self, label: str, value: Any) -> None:
        self.chunks.append((label, value))


async def _make_frontend(repo: Path) -> None:
    """A consumer repo with one file per state the block can report."""
    wide_tests = [f"tests/wide{i}.test.ts" for i in range(_WIDE_TESTS)]
    index = await make_repo_index(
        repo,
        {
            "src/api.ts": [_symbol("src/api.ts::getUser")],
            "src/other.ts": [_symbol("src/other.ts::helper")],
            "src/imported.ts": [_symbol("src/imported.ts::thing")],
            "src/wide.ts": [_symbol("src/wide.ts::wide")],
        },
        alias="frontend",
        graph_nodes=(
            ("src/api.ts", False),
            ("src/other.ts", False),
            ("src/imported.ts", False),
            ("src/wide.ts", False),
            ("tests/api.test.ts", True),
            ("tests/imports.test.ts", True),
            *((path, True) for path in wide_tests),
        ),
        graph_edges=(
            ("src/api.ts", "src/api.ts::getUser", "defines"),
            ("src/other.ts", "src/other.ts::helper", "defines"),
            ("src/imported.ts", "src/imported.ts::thing", "defines"),
            ("src/wide.ts", "src/wide.ts::wide", "defines"),
            ("tests/api.test.ts", "tests/api.test.ts::it_getUser", "defines"),
            ("tests/api.test.ts::it_getUser", "src/api.ts::getUser", "calls"),
            ("tests/imports.test.ts", "src/imported.ts", "imports"),
            *((path, f"{path}::it", "defines") for path in wide_tests),
            *((f"{path}::it", "src/wide.ts::wide", "calls") for path in wide_tests),
        ),
        coverage=(("tests/cov.test.ts::covers", "tests/cov.test.ts", "src/api.ts"),),
    )
    # The helper opens the repo itself; only the database on disk is wanted.
    await index.close()


class _FakeRegistry:
    def __init__(self, root: Path) -> None:
        self.workspace_root = root
        self.ws_config = WorkspaceConfig(
            repos=[RepoEntry(path="frontend", alias="frontend")]
        )


@contextlib.contextmanager
def _in_workspace(root: Path | None, enricher: Any):
    prev = (_state._registry, _state._cross_repo_enricher)
    _state._registry = _FakeRegistry(root) if root is not None else None
    _state._cross_repo_enricher = enricher
    try:
        yield
    finally:
        _state._registry, _state._cross_repo_enricher = prev


async def _make_huge_frontend(repo: Path) -> None:
    """A consumer repo whose one call site is reached by more tests than the cap."""
    tests = [f"tests/huge{i}.test.ts" for i in range(_HUGE_TESTS)]
    index = await make_repo_index(
        repo,
        {"src/huge.ts": [_symbol("src/huge.ts::huge")]},
        alias="frontend",
        graph_nodes=(
            ("src/huge.ts", False),
            *((path, True) for path in tests),
        ),
        graph_edges=(
            ("src/huge.ts", "src/huge.ts::huge", "defines"),
            *((path, f"{path}::it", "defines") for path in tests),
            *((f"{path}::it", "src/huge.ts::huge", "calls") for path in tests),
        ),
    )
    await index.close()


@pytest.fixture
async def workspace(tmp_path: Path):
    """A workspace whose one consumer repo is indexed on disk."""
    await _make_frontend(tmp_path / "frontend")
    try:
        yield tmp_path
    finally:
        await close_test_impact_indexes()


@pytest.fixture
async def huge_workspace(tmp_path: Path):
    """The same workspace, with one call site far more tests reach."""
    await _make_huge_frontend(tmp_path / "frontend")
    try:
        yield tmp_path
    finally:
        await close_test_impact_indexes()


def _block(result: Any, link: ContractLink, collector: Any = None) -> dict[str, Any]:
    return _tests_block_for(
        result,
        link.consumer_repo,
        link.consumer_file,
        link.contract_id,
        collector,
        "cross_repo.consumers[0].tests.tests_to_run",
    )


# ---------------------------------------------------------------------------
# The four states
# ---------------------------------------------------------------------------


async def test_a_covered_call_site_is_measured(workspace: Path) -> None:
    with _in_workspace(workspace, _FakeEnricher([BOUND])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    block = _block(result, BOUND)
    assert block["state"] == "measured"
    assert block["tests_to_run"][0]["test_file"] == "tests/cov.test.ts"
    assert block["tests_to_run"][0]["via"] == "coverage-map"
    assert block["unresolved_reason"] is None
    # A plain number, never a percentage.
    assert block["tests_to_run"][0]["confidence"] == 0.9


async def test_an_uncovered_call_site_falls_back_to_inferred(workspace: Path) -> None:
    with _in_workspace(workspace, _FakeEnricher([ONLY_IMPORTED])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    block = _block(result, ONLY_IMPORTED)
    assert block["state"] == "inferred"
    assert [t["test_file"] for t in block["tests_to_run"]] == ["tests/imports.test.ts"]
    assert block["tests_to_run"][0]["via"] == "import-graph"


async def test_a_call_site_nothing_reaches_says_none(workspace: Path) -> None:
    """The walk ran and came back empty; that is a state, not a silence."""
    with _in_workspace(workspace, _FakeEnricher([NOTHING_REACHES])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    block = _block(result, NOTHING_REACHES)
    assert block["state"] == "none"
    assert block["tests_to_run"] == []
    assert block["total"] == 0
    assert block["unresolved_reason"] is None


async def test_a_link_that_never_bound_carries_its_reason(workspace: Path) -> None:
    with _in_workspace(workspace, _FakeEnricher([UNBOUND])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    block = _block(result, UNBOUND)
    assert block["state"] == "unresolved"
    assert block["unresolved_reason"] == "unbound"
    assert block["tests_to_run"] == []


async def test_a_consumer_repo_without_an_index_says_so(workspace: Path) -> None:
    absent = _link(
        consumer_file="src/client.ts",
        consumer_symbol_id="src/client.ts::call",
        consumer_repo="mobile",
    )
    with _in_workspace(workspace, _FakeEnricher([absent])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    block = _block(result, absent)
    assert block["state"] == "unresolved"
    assert block["unresolved_reason"] == "no_index"


# ---------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------


async def test_the_inline_list_is_capped_and_the_tail_is_recoverable(
    workspace: Path,
) -> None:
    collector = _FakeCollector()
    with _in_workspace(workspace, _FakeEnricher([WIDE])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    block = _block(result, WIDE, collector)
    assert block["state"] == "inferred"
    assert len(block["tests_to_run"]) == _TESTS_PER_CONSUMER_LIMIT
    assert block["total"] == _WIDE_TESTS
    assert block["truncated"] is True
    label, tail = collector.chunks[0]
    assert label == (
        f"cross_repo.consumers[0].tests.tests_to_run beyond "
        f"cap={_TESTS_PER_CONSUMER_LIMIT} ({_WIDE_TESTS - _TESTS_PER_CONSUMER_LIMIT} dropped)"
    )
    assert len(tail) == _WIDE_TESTS - _TESTS_PER_CONSUMER_LIMIT


async def test_the_total_counts_every_test_not_the_join_cap(
    huge_workspace: Path,
) -> None:
    """The helper caps its own inline list, so the join must not cap first."""
    collector = _FakeCollector()
    with _in_workspace(huge_workspace, _FakeEnricher([HUGE])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    block = _block(result, HUGE, collector)
    assert block["total"] == _HUGE_TESTS
    assert block["truncated"] is True
    assert len(block["tests_to_run"]) == _TESTS_PER_CONSUMER_LIMIT
    assert len(collector.chunks[0][1]) == _HUGE_TESTS - _TESTS_PER_CONSUMER_LIMIT


# ---------------------------------------------------------------------------
# When the helper declines
# ---------------------------------------------------------------------------


async def test_absent_outside_workspace_mode(workspace: Path) -> None:
    with _in_workspace(None, _FakeEnricher([BOUND])):
        assert await cross_repo_tests("backend", [PROVIDER_FILE]) is None


async def test_absent_without_contract_data(workspace: Path) -> None:
    enricher = _FakeEnricher([BOUND])
    enricher.has_contract_data = False
    with _in_workspace(workspace, enricher):
        assert await cross_repo_tests("backend", [PROVIDER_FILE]) is None


async def test_no_link_for_the_changed_file_is_named_not_silent(workspace: Path) -> None:
    """Contract data but no link is an answer with a reason, not a None."""
    with _in_workspace(workspace, _FakeEnricher([BOUND])):
        result = await cross_repo_tests("backend", ["app/untouched.py"])
    assert result is not None
    assert result.summary["reason"] == "no_matching_links"
    assert result.recommendations == []


async def test_a_failed_join_names_itself_on_every_row(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A break in the lookup must not read as "no tests guard this"."""
    import repowise.core.workspace.test_impact as core_test_impact

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("index gone")

    monkeypatch.setattr(core_test_impact, "analyze_workspace_test_impact", _boom)
    with _in_workspace(workspace, _FakeEnricher([BOUND])):
        result = await cross_repo_tests("backend", [PROVIDER_FILE])
    assert result is not None
    assert result.summary == {"reason": "lookup_failed", "detail": "RuntimeError"}
    block = _block(result, BOUND)
    assert block["state"] == "unresolved"
    assert block["unresolved_reason"] == "lookup_failed"
    assert block["unresolved_detail"] == "RuntimeError"


async def test_a_none_result_still_yields_a_block_naming_its_state() -> None:
    block = _block(None, BOUND)
    assert block["state"] == "none"
    assert block == {
        "state": "none",
        "tests_to_run": [],
        "total": 0,
        "truncated": False,
        "unresolved_reason": None,
        "unresolved_detail": None,
    }


# ---------------------------------------------------------------------------
# The process-held indexes
# ---------------------------------------------------------------------------


async def test_the_consumer_index_is_opened_once_and_released(workspace: Path) -> None:
    """Opening a repo loads its whole symbol table, so it is held across calls."""
    with _in_workspace(workspace, _FakeEnricher([BOUND])):
        await cross_repo_tests("backend", [PROVIDER_FILE])
        first = _state._test_impact_indexes["frontend"]
        await cross_repo_tests("backend", [PROVIDER_FILE])
        assert _state._test_impact_indexes["frontend"] is first
    await close_test_impact_indexes()
    assert _state._test_impact_indexes == {}


async def test_a_closed_cache_answers_again_on_the_next_call(workspace: Path) -> None:
    """Closing releases the sessions and the lock; the next call rebuilds both."""
    with _in_workspace(workspace, _FakeEnricher([BOUND])):
        first = await cross_repo_tests("backend", [PROVIDER_FILE])
        await close_test_impact_indexes()
        assert _state._test_impact_lock is None
        again = await cross_repo_tests("backend", [PROVIDER_FILE])
    assert first is not None and again is not None
    assert [r.test_id for r in again.recommendations] == [
        r.test_id for r in first.recommendations
    ]
    assert _state._test_impact_indexes["frontend"] is not None
