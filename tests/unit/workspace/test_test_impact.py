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
from repowise.core.workspace.contracts import (
    ContractLink,
    ContractStore,
    save_contract_store,
)
from repowise.core.workspace.repo_index import (
    RepoIndex,
    WorkspaceIndex,
    open_workspace_index,
)
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
#: Nothing calls ``src/imported.ts``; one test file imports it, so only the
#: weaker file-level tier can answer for it.
ONLY_IMPORTED = _link(
    consumer_file="src/imported.ts", consumer_symbol_id="src/imported.ts::thing"
)

ALL_LINKS = [BOUND, UNBOUND, NO_INDEX, NOTHING_REACHES, SYMBOL_GONE]


async def _frontend(repo: Path) -> RepoIndex:
    """A consumer repo whose two tests each reach one symbol of ``src/api.ts``."""
    return await make_repo_index(
        repo,
        {
            "src/api.ts": [_symbol("src/api.ts::getUser"), _symbol("src/api.ts::listUsers")],
            "src/other.ts": [_symbol("src/other.ts::helper")],
            "src/imported.ts": [_symbol("src/imported.ts::thing")],
        },
        alias="frontend",
        graph_nodes=(
            ("src/api.ts", False),
            ("src/other.ts", False),
            ("src/imported.ts", False),
            ("tests/api.test.ts", True),
            ("tests/list.test.ts", True),
            ("tests/imports.test.ts", True),
        ),
        graph_edges=(
            ("src/api.ts", "src/api.ts::getUser", "defines"),
            ("src/api.ts", "src/api.ts::listUsers", "defines"),
            ("src/other.ts", "src/other.ts::helper", "defines"),
            ("src/imported.ts", "src/imported.ts::thing", "defines"),
            ("tests/api.test.ts", "tests/api.test.ts::it_getUser", "defines"),
            ("tests/list.test.ts", "tests/list.test.ts::it_listUsers", "defines"),
            ("tests/api.test.ts::it_getUser", "src/api.ts::getUser", "calls"),
            ("tests/list.test.ts::it_listUsers", "src/api.ts::listUsers", "calls"),
            ("tests/imports.test.ts", "src/imported.ts", "imports"),
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
        assert by_basis["inferred"].consumer_symbol_ids == ["src/api.ts::getUser"]
        assert by_basis["inferred"].consumer_files == ["src/api.ts"]
        assert by_basis["inferred"].contract_ids == [BOUND.contract_id]
        assert by_basis["inferred"].contract_types == ["http"]
        assert by_basis["inferred"].source_files == [PROVIDER_FILE]

    async def test_a_link_that_never_bound_says_so(self, workspace: WorkspaceIndex) -> None:
        result = await analyze_workspace_test_impact(workspace, [UNBOUND], CHANGED)

        assert [u.reason for u in result.unresolved] == ["unbound"]
        assert result.unresolved[0].consumer_file == "src/legacy.ts"
        assert result.unresolved[0].contract_id == UNBOUND.contract_id
        assert result.unresolved[0].contract_type == "http"
        assert result.unresolved[0].provider_file == PROVIDER_FILE
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
        assert states["unresolved"] == 2, "src/legacy.ts, and mobile's src/client.ts"
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


async def test_the_join_runs_against_the_indexed_consumer(
    frontend_index: RepoIndex, workspace: WorkspaceIndex, tmp_path: Path
) -> None:
    """The index the join queries is the one on disk, keyed by its own path."""
    assert frontend_index.repo_path == tmp_path / "frontend"
    assert frontend_index.repo_id

    result = await analyze_workspace_test_impact(
        workspace, [BOUND], CHANGED, include_measured=False
    )
    assert result.recommendations


async def test_from_root_resolves_the_consumer_by_path_not_by_alias(
    tmp_path: Path,
) -> None:
    """The regression: the alias is ``frontend``, the directory is ``web-app``.

    Opening the consumer's index by its alias would look for ``frontend/`` and
    find nothing, and the command would report zero tests with no unresolved
    row to explain it.
    """
    (tmp_path / "backend").mkdir()
    index = await _frontend(tmp_path / "web-app")
    await index.close()

    WorkspaceConfig(
        repos=[
            RepoEntry(path="backend", alias="backend", is_primary=True),
            RepoEntry(path="web-app", alias="frontend"),
        ]
    ).save(tmp_path)
    save_contract_store(ContractStore(contract_links=[BOUND]), tmp_path)

    result = await workspace_test_impact_from_root(tmp_path, CHANGED)

    inferred = [rec for rec in result.recommendations if rec.basis == "inferred"]
    assert [rec.test_file for rec in inferred] == ["tests/api.test.ts"]
    assert inferred[0].consumer_repo == "frontend"


# ---------------------------------------------------------------------------
# One row per test per consumer and provider pair
# ---------------------------------------------------------------------------


async def test_two_contracts_on_one_file_are_one_row_naming_both(
    workspace: WorkspaceIndex,
) -> None:
    """Two endpoints in the same handler file do not double the test list."""
    second = _link(
        consumer_file="src/api.ts",
        consumer_symbol_id="src/api.ts::getUser",
        contract_id="http::POST::/users",
    )
    result = await analyze_workspace_test_impact(
        workspace, [BOUND, second], CHANGED, include_measured=False
    )

    assert len(result.recommendations) == 1
    assert result.recommendations_total == 1
    row = result.recommendations[0]
    assert row.contract_ids == sorted([BOUND.contract_id, second.contract_id])
    assert row.contract_types == ["http"]
    assert row.source_files == [PROVIDER_FILE]


async def test_a_test_guarding_two_changed_files_sorts_first(
    workspace: WorkspaceIndex,
) -> None:
    other_provider = "app/routers/orders.py"
    third_provider = "app/routers/listing.py"
    result = await analyze_workspace_test_impact(
        workspace,
        [
            BOUND,
            _link(
                consumer_file="src/api.ts",
                consumer_symbol_id="src/api.ts::getUser",
                contract_id="http::GET::/orders",
                provider_file=other_provider,
            ),
            _link(
                consumer_file="src/imported.ts",
                consumer_symbol_id="src/imported.ts::thing",
                contract_id="http::GET::/listing",
                provider_file=third_provider,
            ),
        ],
        [
            *CHANGED,
            {"repo": "backend", "path": other_provider},
            {"repo": "backend", "path": third_provider},
        ],
        include_measured=False,
    )

    assert [rec.test_file for rec in result.recommendations] == [
        "tests/api.test.ts",
        "tests/imports.test.ts",
    ]
    assert result.recommendations[0].source_files == sorted([PROVIDER_FILE, other_provider])
    assert result.recommendations[1].source_files == [third_provider]


async def test_the_evidence_says_where_each_tier_entered(
    workspace: WorkspaceIndex,
) -> None:
    """The call walk enters at a symbol; the import fallback is file level."""
    result = await analyze_workspace_test_impact(
        workspace, [BOUND, ONLY_IMPORTED], CHANGED, include_measured=False
    )

    by_via = {rec.via: rec for rec in result.recommendations}
    assert by_via["call-graph"].evidence[0]["entry"] == "symbol"
    assert by_via["call-graph"].evidence[0]["contract_id"] == BOUND.contract_id
    assert by_via["import-graph"].test_file == "tests/imports.test.ts"
    assert by_via["import-graph"].evidence[0]["entry"] == "file"


async def test_a_measured_row_says_it_entered_at_the_file(
    workspace: WorkspaceIndex,
) -> None:
    """Coverage rows are per file, so the measured tier claims no symbol."""
    result = await analyze_workspace_test_impact(
        workspace, [BOUND], CHANGED, include_inferred=False
    )

    entry = result.recommendations[0].evidence[0]
    assert entry["basis"] == "measured"
    assert entry["entry"] == "file"
    assert entry["contract_id"] == BOUND.contract_id


# ---------------------------------------------------------------------------
# Failure is reported, never swallowed
# ---------------------------------------------------------------------------


async def test_a_failing_pass_keeps_what_the_other_pass_found(
    workspace: WorkspaceIndex, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The inferred walk falls over; the coverage rows it never touched stay."""
    from repowise.core.workspace import test_impact as test_impact_mod

    async def boom(*args, **kwargs):
        raise RuntimeError("connection went away")

    monkeypatch.setattr(test_impact_mod, "tests_reaching_by_tier", boom)

    result = await analyze_workspace_test_impact(
        workspace, [BOUND, UNBOUND, NOTHING_REACHES], CHANGED
    )

    assert [rec.basis for rec in result.recommendations] == ["measured"]
    assert result.recommendations[0].test_id == "tests/cov.test.ts::covers"

    by_reason: dict[str, list] = {}
    for link in result.unresolved:
        by_reason.setdefault(link.reason, []).append(link)
    assert sorted(by_reason) == ["lookup_failed", "unbound"]
    assert {u.detail for u in by_reason["lookup_failed"]} == {"inferred: RuntimeError"}
    assert [u.consumer_file for u in by_reason["unbound"]] == ["src/legacy.ts"]


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
        assert {
            contract_id
            for rec in result.recommendations
            for contract_id in rec.contract_ids
        } == {BOUND.contract_id}

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
        assert result.summary["passes"] == {"measured": False, "inferred": True}

    async def test_inferred_can_be_excluded(self, workspace: WorkspaceIndex) -> None:
        result = await analyze_workspace_test_impact(
            workspace, [BOUND], CHANGED, include_inferred=False
        )

        assert {rec.basis for rec in result.recommendations} == {"measured"}
        assert result.summary["passes"]["inferred"] is False

    async def test_both_passes_are_reported_as_run_by_default(
        self, workspace: WorkspaceIndex
    ) -> None:
        result = await analyze_workspace_test_impact(workspace, [BOUND], CHANGED)

        assert result.summary["passes"] == {"measured": True, "inferred": True}
        assert result.recommendations_by_basis == {"measured": 1, "inferred": 1}


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
    assert result.summary["reason"] == "no_matching_links"
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


async def test_a_file_row_counts_the_tests_the_walk_found(
    workspace: WorkspaceIndex,
) -> None:
    result = await analyze_workspace_test_impact(
        workspace, [BOUND], CHANGED, include_measured=False
    )

    row = next(f for f in result.files_analyzed if f["consumer_file"] == "src/api.ts")
    assert row["inferred_tests_count"] == 1
    assert row["measured_tests_count"] == 0
    assert "inferred_tests_total" not in row


async def test_the_json_mirrors_the_dataclasses(workspace: WorkspaceIndex) -> None:
    result = await analyze_workspace_test_impact(workspace, ALL_LINKS, CHANGED)
    payload = workspace_test_impact_to_dict(result)

    assert payload["workspace"] is True
    assert payload["recommendations_emitted"] == len(result.recommendations)
    assert len(payload["unresolved"]) == len(result.unresolved)
    assert {u["reason"] for u in payload["unresolved"]} == {
        u.reason for u in result.unresolved
    }
    row = payload["recommendations"][0]
    assert row["contract_ids"] == result.recommendations[0].contract_ids
    assert row["contract_types"] == result.recommendations[0].contract_types
    assert "provider_file" not in row
    assert "contract_id" not in row


async def test_a_workspace_without_a_contract_store_says_which_step_is_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / "backend").mkdir()
    WorkspaceConfig(repos=[RepoEntry(path="backend", alias="backend")]).save(tmp_path)

    result = await workspace_test_impact_from_root(tmp_path, CHANGED)

    assert result.summary["reason"] == "no_contract_store"
    assert not result.recommendations


async def test_only_the_named_aliases_are_opened(tmp_path: Path) -> None:
    """The join opens the consumers it needs, not the whole workspace."""
    for alias, directory in (("frontend", "web-app"), ("backend", "backend")):
        index = await make_repo_index(
            tmp_path / directory,
            {"src/api.ts": [_symbol("src/api.ts::getUser")]},
            alias=alias,
        )
        await index.close()

    ws_config = WorkspaceConfig(
        repos=[
            RepoEntry(path="backend", alias="backend"),
            RepoEntry(path="web-app", alias="frontend"),
        ]
    )
    workspace = await open_workspace_index(ws_config, tmp_path, aliases={"frontend"})
    try:
        assert workspace.get("frontend") is not None
        assert workspace.get("backend") is None
    finally:
        await workspace.close()


async def test_two_links_in_one_file_each_get_only_their_own_symbols_tests(
    workspace: WorkspaceIndex,
) -> None:
    """Symbol entry holds link by link, not file by file.

    ``getUser`` and ``listUsers`` share ``src/api.ts``; each is reached by one
    test, and neither link may be credited with the other's.
    """
    list_link = _link(
        consumer_file="src/api.ts",
        consumer_symbol_id="src/api.ts::listUsers",
        contract_id="http::GET::/users",
    )
    result = await analyze_workspace_test_impact(
        workspace, [BOUND, list_link], CHANGED, include_measured=False
    )

    by_test = {rec.test_file: rec.contract_ids for rec in result.recommendations}
    assert by_test == {
        "tests/api.test.ts": [BOUND.contract_id],
        "tests/list.test.ts": ["http::GET::/users"],
    }
