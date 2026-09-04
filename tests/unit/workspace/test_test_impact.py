"""Cross-repo test impact: the join from a provider change to consumer tests.

The join runs against a real per-repo index, because the two things it has to
get right are both query-path facts. A consumer repo is named by its alias but
its row is keyed by ``local_path``, so a lookup by alias silently finds nothing
and the command reports zero tests. And a contract link names a *symbol*, so
entering the call walk at the file would recommend every test that reaches any
symbol in it.

The other claim under test is that an empty answer is never silent: a link that
cannot be followed becomes an ``unresolved`` row saying why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.models import Symbol
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.contracts import ContractLink
from repowise.core.workspace.repo_index import RepoIndex, WorkspaceIndex
from repowise.core.workspace.test_impact import (
    analyze_workspace_test_impact,
    workspace_test_impact_from_root,
    workspace_test_impact_to_dict,
)

from ._repo_index import make_repo_index

PROVIDER_FILE = "app/routers/users.py"
CHANGED = [{"repo": "backend", "path": PROVIDER_FILE}]


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
    contract_id: str = "http::GET::/users/{param}",
    confidence: float = 0.9,
    provider_repo: str = "backend",
    provider_file: str = PROVIDER_FILE,
) -> ContractLink:
    return ContractLink(
        contract_id=contract_id,
        contract_type="http",
        match_type="exact",
        confidence=confidence,
        provider_repo=provider_repo,
        provider_file=provider_file,
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
NO_INDEX = _link(
    consumer_file="src/client.ts",
    consumer_symbol_id="src/client.ts::call",
    consumer_repo="mobile",
)
NOTHING_REACHES = _link(
    consumer_file="src/other.ts", consumer_symbol_id="src/other.ts::helper"
)
SYMBOL_GONE = _link(consumer_file="src/api.ts", consumer_symbol_id="src/api.ts::gone")

ALL_LINKS = [BOUND, UNBOUND, NO_INDEX, NOTHING_REACHES, SYMBOL_GONE]


async def _frontend(repo: Path) -> RepoIndex:
    """A consumer repo whose two tests each reach one symbol of ``src/api.ts``."""
    return await make_repo_index(
        repo,
        {
            "src/api.ts": [_symbol("src/api.ts::getUser"), _symbol("src/api.ts::listUsers")],
            "src/other.ts": [_symbol("src/other.ts::helper")],
        },
        alias="frontend",
        graph_nodes=(
            ("src/api.ts", False),
            ("src/other.ts", False),
            ("tests/api.test.ts", True),
            ("tests/list.test.ts", True),
        ),
        graph_edges=(
            ("src/api.ts", "src/api.ts::getUser", "defines"),
            ("src/api.ts", "src/api.ts::listUsers", "defines"),
            ("src/other.ts", "src/other.ts::helper", "defines"),
            ("tests/api.test.ts", "tests/api.test.ts::it_getUser", "defines"),
            ("tests/list.test.ts", "tests/list.test.ts::it_listUsers", "defines"),
            ("tests/api.test.ts::it_getUser", "src/api.ts::getUser", "calls"),
            ("tests/list.test.ts::it_listUsers", "src/api.ts::listUsers", "calls"),
        ),
        coverage=(("tests/cov.test.ts::covers", "tests/cov.test.ts", "src/api.ts"),),
    )


@pytest.fixture
async def frontend_index(tmp_path: Path):
    index = await _frontend(tmp_path / "frontend")
    try:
        yield index
    finally:
        await index.close()


@pytest.fixture
def workspace(frontend_index: RepoIndex) -> WorkspaceIndex:
    return WorkspaceIndex({"frontend": frontend_index})


# ---------------------------------------------------------------------------
# The four states
# ---------------------------------------------------------------------------


class TestStates:
    async def test_a_bound_link_yields_both_a_measured_and_an_inferred_row(
        self, workspace: WorkspaceIndex
    ) -> None:
        result = await analyze_workspace_test_impact(workspace, [BOUND], CHANGED)

        by_basis = {rec.basis: rec for rec in result.recommendations}
        assert by_basis["measured"].test_id == "tests/cov.test.ts::covers"
        assert by_basis["measured"].via == "coverage-map"
        assert by_basis["inferred"].test_file == "tests/api.test.ts"
        assert by_basis["inferred"].via == "call-graph"
        assert by_basis["inferred"].consumer_symbol_id == "src/api.ts::getUser"

    async def test_a_link_that_never_bound_says_so(self, workspace: WorkspaceIndex) -> None:
        result = await analyze_workspace_test_impact(workspace, [UNBOUND], CHANGED)

        assert [u.reason for u in result.unresolved] == ["unbound"]
        assert result.unresolved[0].consumer_file == "src/legacy.ts"
        assert not result.recommendations

    async def test_a_consumer_without_an_index_says_so(
        self, workspace: WorkspaceIndex
    ) -> None:
        result = await analyze_workspace_test_impact(workspace, [NO_INDEX], CHANGED)

        assert [u.reason for u in result.unresolved] == ["no_index"]
        assert result.summary["consumer_repos_without_index"] == ["mobile"]

    async def test_a_call_site_nothing_reaches_is_analysed_and_empty(
        self, workspace: WorkspaceIndex
    ) -> None:
        """``none`` is a result: the walk ran, and no test came back."""
        result = await analyze_workspace_test_impact(workspace, [NOTHING_REACHES], CHANGED)

        assert not result.recommendations
        rows = [f for f in result.files_analyzed if f["consumer_file"] == "src/other.ts"]
        assert [row["state"] for row in rows] == ["none"]

    async def test_a_symbol_the_index_no_longer_holds_says_so(
        self, workspace: WorkspaceIndex
    ) -> None:
        result = await analyze_workspace_test_impact(workspace, [SYMBOL_GONE], CHANGED)

        assert [u.reason for u in result.unresolved] == ["symbol_missing"]
        assert result.unresolved[0].consumer_symbol_id == "src/api.ts::gone"

    async def test_the_state_counts_agree_with_the_rows(
        self, workspace: WorkspaceIndex
    ) -> None:
        result = await analyze_workspace_test_impact(workspace, ALL_LINKS, CHANGED)

        states = result.summary["states"]
        assert states["measured"] == 1, "src/api.ts, from the coverage map"
        assert states["inferred"] == 0, "measured wins on the one file that has both"
        assert states["none"] == 1, "src/other.ts"
        assert states["unresolved"] >= 1
        assert sum(states.values()) == len(result.files_analyzed)
        assert sorted(u.reason for u in result.unresolved) == [
            "no_index",
            "symbol_missing",
            "unbound",
        ]
        assert result.summary["consumer_repos_analyzed"] == ["frontend"]
        assert result.summary["total_contract_links"] == 5


# ---------------------------------------------------------------------------
# Entry at the symbol, not the file
# ---------------------------------------------------------------------------


async def test_a_test_reaching_another_symbol_in_the_same_file_is_not_recommended(
    workspace: WorkspaceIndex,
) -> None:
    """The regression for a file-keyed join.

    ``tests/list.test.ts`` reaches ``listUsers``, which lives in the linked
    file but is not the linked symbol, so the contract does not endanger it.
    """
    result = await analyze_workspace_test_impact(
        workspace, [BOUND], CHANGED, include_measured=False
    )

    reached = {rec.test_file for rec in result.recommendations}
    assert reached == {"tests/api.test.ts"}


async def test_the_consumer_row_is_found_by_path_not_by_alias(
    frontend_index: RepoIndex, workspace: WorkspaceIndex, tmp_path: Path
) -> None:
    """The alias is ``frontend``; the ``repositories`` row is keyed by path."""
    assert frontend_index.repo_path == tmp_path / "frontend"
    assert frontend_index.repo_id

    result = await analyze_workspace_test_impact(
        workspace, [BOUND], CHANGED, include_measured=False
    )
    assert result.recommendations, "an alias lookup would have returned nothing"


# ---------------------------------------------------------------------------
# Failure is reported, never swallowed
# ---------------------------------------------------------------------------


async def test_a_failing_lookup_becomes_an_unresolved_row(
    workspace: WorkspaceIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.workspace import test_impact as test_impact_mod

    async def boom(*args, **kwargs):
        raise RuntimeError("connection went away")

    monkeypatch.setattr(test_impact_mod, "tests_reaching_by_tier", boom)

    result = await analyze_workspace_test_impact(
        workspace, [BOUND, NOTHING_REACHES], CHANGED
    )

    assert not result.recommendations
    assert len(result.unresolved) == 2
    assert {u.reason for u in result.unresolved} == {"lookup_failed"}
    assert {u.detail for u in result.unresolved} == {"RuntimeError"}


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


class TestFilters:
    async def test_min_confidence_drops_the_weaker_link(
        self, workspace: WorkspaceIndex
    ) -> None:
        weak = _link(
            consumer_file="src/api.ts",
            consumer_symbol_id="src/api.ts::getUser",
            contract_id="http::POST::/users",
            confidence=0.5,
        )
        result = await analyze_workspace_test_impact(
            workspace, [BOUND, weak], CHANGED, min_confidence=0.8
        )

        assert result.summary["relevant_contract_links"] == 1
        assert {rec.contract_id for rec in result.recommendations} == {BOUND.contract_id}

    async def test_target_repos_limits_the_consumers_considered(
        self, workspace: WorkspaceIndex
    ) -> None:
        result = await analyze_workspace_test_impact(
            workspace, [BOUND, NO_INDEX], CHANGED, target_repos=["frontend"]
        )

        assert result.summary["relevant_contract_links"] == 1
        assert not result.unresolved, "the mobile link was never considered"

    async def test_measured_can_be_excluded(self, workspace: WorkspaceIndex) -> None:
        result = await analyze_workspace_test_impact(
            workspace, [BOUND], CHANGED, include_measured=False
        )

        assert {rec.basis for rec in result.recommendations} == {"inferred"}

    async def test_inferred_can_be_excluded(self, workspace: WorkspaceIndex) -> None:
        result = await analyze_workspace_test_impact(
            workspace, [BOUND], CHANGED, include_inferred=False
        )

        assert {rec.basis for rec in result.recommendations} == {"measured"}


# ---------------------------------------------------------------------------
# What the join skips, and what it reports about the change itself
# ---------------------------------------------------------------------------


async def test_a_link_inside_one_repo_and_service_is_not_cross_repo(
    workspace: WorkspaceIndex,
) -> None:
    same_repo = _link(
        consumer_file="src/api.ts",
        consumer_symbol_id="src/api.ts::getUser",
        provider_repo="frontend",
        provider_file="src/server.ts",
    )
    result = await analyze_workspace_test_impact(
        workspace, [same_repo], [{"repo": "frontend", "path": "src/server.ts"}]
    )

    assert result.summary["relevant_contract_links"] == 0
    assert not result.recommendations


async def test_a_changed_file_no_contract_names_is_reported(
    workspace: WorkspaceIndex,
) -> None:
    changed = [*CHANGED, {"repo": "backend", "path": "app/util.py"}]
    result = await analyze_workspace_test_impact(workspace, [BOUND], changed)

    assert result.summary["changed_files_without_contracts"] == {
        "backend": ["app/util.py"]
    }
    assert result.summary["changed_provider_files"] == {
        "backend": [PROVIDER_FILE, "app/util.py"]
    }


async def test_a_windows_path_still_matches_its_contract(
    workspace: WorkspaceIndex,
) -> None:
    result = await analyze_workspace_test_impact(
        workspace,
        [BOUND],
        [{"repo": "backend", "path": "app\\routers\\users.py"}],
        include_measured=False,
    )

    assert result.recommendations


# ---------------------------------------------------------------------------
# Bounds and provenance
# ---------------------------------------------------------------------------


async def test_the_cap_says_how_many_it_dropped(tmp_path: Path) -> None:
    """Sixty tests call the linked symbol; fifty are emitted and it says so."""
    from repowise.core.analysis.test_reachability import MAX_TESTS_PER_TARGET

    tests = [f"tests/t{i:02d}.test.ts" for i in range(60)]
    index = await make_repo_index(
        tmp_path / "frontend",
        {"src/api.ts": [_symbol("src/api.ts::getUser")]},
        alias="frontend",
        graph_nodes=(("src/api.ts", False), *((t, True) for t in tests)),
        graph_edges=(
            ("src/api.ts", "src/api.ts::getUser", "defines"),
            *((t, f"{t}::it", "defines") for t in tests),
            *((f"{t}::it", "src/api.ts::getUser", "calls") for t in tests),
        ),
    )
    try:
        result = await analyze_workspace_test_impact(
            WorkspaceIndex({"frontend": index}), [BOUND], CHANGED, include_measured=False
        )
    finally:
        await index.close()

    assert result.recommendations_total == 60
    assert result.recommendations_emitted == MAX_TESTS_PER_TARGET == 50
    assert result.recommendations_truncated is True
    assert result.recommendations_omitted == 10
    assert len(result.recommendations) == 50


async def test_an_inferred_row_carries_the_link_confidence_unchanged(
    workspace: WorkspaceIndex,
) -> None:
    link = _link(
        consumer_file="src/api.ts",
        consumer_symbol_id="src/api.ts::getUser",
        confidence=0.83,
    )
    result = await analyze_workspace_test_impact(
        workspace, [link], CHANGED, include_measured=False
    )

    assert [rec.confidence for rec in result.recommendations] == [0.83]


async def test_the_json_mirrors_the_dataclasses(workspace: WorkspaceIndex) -> None:
    result = await analyze_workspace_test_impact(workspace, ALL_LINKS, CHANGED)
    payload = workspace_test_impact_to_dict(result)

    assert payload["workspace"] is True
    assert payload["recommendations_emitted"] == len(result.recommendations)
    assert len(payload["unresolved"]) == len(result.unresolved)
    assert {u["reason"] for u in payload["unresolved"]} == {
        u.reason for u in result.unresolved
    }


async def test_a_workspace_without_a_contract_store_says_which_step_is_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    WorkspaceConfig(repos=[RepoEntry(path="backend", alias="backend")]).save(tmp_path)

    result = await workspace_test_impact_from_root(tmp_path, CHANGED)

    assert result.summary["reason"] == "no_contract_store"
    assert not result.recommendations
