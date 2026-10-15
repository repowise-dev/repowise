"""Chart datasets read off a fixture index, and the chat-only ``chart`` tool over them."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from repowise.core.persistence.models import (
    Change,
    CoverageFile,
    HealthFileMetric,
    HealthSnapshot,
    KnowledgeGraphLayer,
)
from repowise.server import chat_datasets
from repowise.server.chat_chart import chart_schema, run_chart
from repowise.server.chat_datasets.files import directories, unique_suffixes
from repowise.server.chat_views import _ATTRIBUTION, _PERSON, _RISK_SCORE, TurnViews
from repowise.server.schemas.views import ViewPartList


@pytest.fixture(autouse=True)
def _fresh_cache():
    chat_datasets.clear_cache()
    yield
    chat_datasets.clear_cache()


@pytest.fixture
async def chart_index(session, setup_mcp, health_data: str) -> str:
    rid = health_data
    session.add(
        CoverageFile(
            repository_id=rid,
            file_path="src/auth/service.py",
            source_format="lcov",
            line_coverage_pct=50.0,
            total_coverable_lines=100,
            covered_line_count=50,
        )
    )
    session.add(
        CoverageFile(
            repository_id=rid,
            file_path="src/db/models.py",
            source_format="lcov",
            line_coverage_pct=100.0,
            total_coverable_lines=300,
            covered_line_count=300,
        )
    )
    for i, (day, category) in enumerate([(5, "fix"), (6, "fix"), (14, "feature")]):
        session.add(
            Change(
                repository_id=rid,
                change_id=f"c{i}",
                identity_kind="sha",
                identity_value=f"{i:040d}",
                shape="commit",
                head_sha=f"{i:040d}",
                merged_at=datetime(2026, 10, day, 12, tzinfo=UTC),
                category=category,
                files_changed=i + 1,
            )
        )
    for taken_at, average in [(datetime(2026, 10, 1), 7.5), (datetime(2026, 10, 8), 7.9)]:
        session.add(
            HealthSnapshot(
                id=uuid.uuid4().hex,
                repository_id=rid,
                taken_at=taken_at,
                average_health=average,
                hotspot_health=6.0,
            )
        )
    session.add(
        KnowledgeGraphLayer(
            repository_id=rid,
            layer_id="layer:service",
            name="Service",
            node_ids_json=json.dumps(["file:src/auth/service.py", "file:src/db/models.py"]),
            display_order=0,
        )
    )
    await session.commit()
    return rid


async def _rows(name: str) -> list[dict]:
    return (await chat_datasets.load_rows(name, None)).rows


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------


async def test_files_join_health_git_and_coverage(chart_index):
    files = {r["path"]: r for r in await _rows("files")}
    auth = files["src/auth/service.py"]
    assert auth["health_score"] == 4.5 and auth["health_band"] == "needs work"
    assert auth["commits_90d"] == 8 and auth["package"] == "auth"
    assert auth["coverage_pct"] == 50.0 and auth["coverable_lines"] == 100
    assert files["src/db/models.py"]["health_band"] == "excellent"


async def test_rows_are_cached_per_index_state(chart_index):
    first = await chat_datasets.load_rows("files", None)
    second = await chat_datasets.load_rows("files", None)
    assert not first.cached and second.cached
    assert second.rows is first.rows


async def test_cached_rows_returns_only_what_a_chart_already_read(chart_index):
    assert await chat_datasets.cached_rows(["files", "changes"], None) == {}
    files = await _rows("files")
    cached = await chat_datasets.cached_rows(["files", "changes"], None)
    assert cached == {"files": files}


async def test_findings_dead_code_coverage_history_and_layers(chart_index):
    findings = await _rows("findings")
    assert {f["kind"] for f in findings} >= {"complex_method", "io_in_loop"}
    assert {f["package"] for f in findings} == {"auth"}
    dead = {d["path"]: d for d in await _rows("dead_code")}
    assert dead["src/legacy/old_auth.py"]["tier"] == "high"
    coverage = await _rows("coverage")
    assert sum(c["covered_lines"] for c in coverage) == 350
    changes = await _rows("changes")
    assert [c["week"] for c in changes] == ["2026-10-05", "2026-10-05", "2026-10-12"]
    assert [t["average_health"] for t in await _rows("health_trend")] == [7.5, 7.9]
    assert await _rows("layers") == [{"layer": "Service", "files": 2, "order": 1}]


def test_no_dataset_field_names_a_person_a_risk_score_or_attribution():
    for name in chat_datasets.dataset_names():
        for key in chat_datasets.get_dataset(name).fields:
            assert not _PERSON.search(key), (name, key)
            assert not _RISK_SCORE.search(key), (name, key)
            assert not _ATTRIBUTION.search(key), (name, key)


def test_labels_are_the_shortest_unique_suffix():
    labels = unique_suffixes(["a/update_cmd/command.py", "a/init_cmd/command.py", "b/x.py"])
    assert labels == {
        "a/update_cmd/command.py": "update_cmd/command.py",
        "a/init_cmd/command.py": "init_cmd/command.py",
        "b/x.py": "x.py",
    }


def test_directories_look_past_wrapper_levels():
    packages = {
        **{f"packages/core/src/repowise/core/{d}/f{i}.py": "packages/core" for d in ("a", "b") for i in range(5)},
        "packages/core/alembic/env.py": "packages/core",
        "README.md": "(root)",
    }
    dirs = directories(packages)
    assert dirs["packages/core/src/repowise/core/a/f0.py"] == "packages/core/src/repowise/core/a"
    assert dirs["packages/core/alembic/env.py"] == "packages/core/alembic"
    assert dirs["README.md"] == "(root)"


# ---------------------------------------------------------------------------
# The chart tool
# ---------------------------------------------------------------------------


async def test_chart_draws_a_dataset_in_one_call_with_exact_figures(chart_index):
    views = TurnViews()
    receipt = await run_chart({"dataset": "files", "x": "health_band", "kind": "donut"}, None, views)
    assert receipt["chart_type"] == "donut" and receipt["view_id"] == "v1"
    assert receipt["figures"]["total"] == 2
    assert {s["pct"] for s in receipt["figures"]["shares"]} == {50.0}
    part = views.parts[0]
    assert part["source"]["tool_name"] == "chart"
    ViewPartList.model_validate(views.parts)


async def test_chart_weights_coverage_by_coverable_lines(chart_index):
    views = TurnViews()
    receipt = await run_chart(
        {"dataset": "coverage", "x": "package", "y": "coverage_pct", "aggregate": "avg"}, None, views
    )
    # (50 * 100 + 100 * 300) / 400: the line-weighted share, not the mean of 50 and 100.
    assert receipt["figures"]["overall"] == {"Average coverage pct": 87.5}
    assert "weighted by coverable_lines" in receipt["notes"][0]
    plain = await run_chart(
        {"dataset": "coverage", "x": "package", "y": "coverage_pct", "aggregate": "avg", "weight": "none"},
        None,
        views,
    )
    assert plain["figures"]["overall"] == {"Average coverage pct": 75.0}


async def test_chart_counts_changes_by_week_and_category_as_a_heatmap(chart_index):
    views = TurnViews()
    receipt = await run_chart(
        {"dataset": "changes", "x": "week", "series": "category", "kind": "heatmap"}, None, views
    )
    assert receipt["chart_type"] == "heatmap"
    rows = views.parts[0]["data"]["rows"]
    assert rows == [{"week": "2026-10-05", "s1": 2, "s2": 0}, {"week": "2026-10-12", "s1": 0, "s2": 1}]


async def test_chart_errors_carry_a_spec_that_draws(chart_index):
    views = TurnViews()
    result = await run_chart({"dataset": "files", "x": "healthiness", "y": "nloc"}, None, views)
    assert result["error_code"] == "bad_spec"
    suggested = result["suggested_spec"]
    assert suggested["dataset"] == "files"
    assert "error" not in await run_chart(suggested, None, views)


async def test_chart_refuses_people_and_unknown_datasets(chart_index):
    views = TurnViews()
    people = await run_chart({"dataset": "files", "x": "primary_owner"}, None, views)
    assert people["error_code"] in ("person_grouping", "bad_spec")
    assert views.parts == []
    unknown = await run_chart({"dataset": "owners", "x": "a"}, None, views)
    assert unknown["error_code"] == "unknown_dataset"


async def test_chart_with_only_a_dataset_describes_its_fields(chart_index):
    found = await run_chart({"dataset": "files"}, None, TurnViews())
    (entry,) = found["datasets"]
    assert "health_score" in entry["fields"] and entry["example"]


def test_the_schema_lists_every_dataset_and_its_fields():
    schema = chart_schema()["function"]
    assert schema["parameters"]["properties"]["dataset"]["enum"] == chat_datasets.dataset_names()
    assert "files: path, file, package" in schema["description"]


# ---------------------------------------------------------------------------
# Parity, weeks, freshness and the cache
# ---------------------------------------------------------------------------


async def test_the_repo_average_health_is_get_healths(chart_index, session):
    from repowise.core.analysis.health.scoring import nloc_weighted_score
    from repowise.core.persistence.crud import get_health_metrics
    from repowise.server.mcp_server import get_health

    # A file with no lines still weighs 1, as nloc_weighted_score floors it.
    await session.execute(
        HealthFileMetric.__table__.update()
        .where(HealthFileMetric.file_path == "src/db/models.py")
        .values(nloc=0)
    )
    await session.commit()
    receipt = await run_chart(
        {"dataset": "files", "x": "package", "y": "health_score", "aggregate": "avg"},
        None,
        TurnViews(),
    )
    overall = receipt["figures"]["overall"]["Average health score"]
    metrics = await get_health_metrics(session, chart_index)
    assert overall == round(nloc_weighted_score(metrics), 2)
    assert overall == (await get_health(only=["kpis"]))["kpis"]["average_health"]


async def test_empty_weeks_count_zero(chart_index, session):
    session.add(
        Change(
            repository_id=chart_index,
            change_id="late",
            identity_kind="sha",
            identity_value="9" * 40,
            shape="commit",
            head_sha="9" * 40,
            merged_at=datetime(2026, 10, 27, 12, tzinfo=UTC),
            category="fix",
        )
    )
    await session.commit()
    views = TurnViews()
    await run_chart({"dataset": "changes", "x": "week"}, None, views)
    rows = views.parts[0]["data"]["rows"]
    assert [(r["week"], r["count"]) for r in rows] == [
        ("2026-10-05", 2),
        ("2026-10-12", 1),
        ("2026-10-19", 0),
        ("2026-10-26", 1),
    ]


async def test_freshness_is_read_on_every_call(chart_index, monkeypatch):
    import repowise.server.mcp_server._meta as meta_mod

    await chat_datasets.load_rows("files", None)
    monkeypatch.setattr(
        meta_mod, "build_meta", lambda **_: {"indexed_commit": "abc1234", "stale_warning": "behind"}
    )
    again = await chat_datasets.load_rows("files", None)
    assert again.cached and again.stale and again.as_of == "abc1234"


def test_the_cache_drops_a_repos_older_state_and_evicts_the_oldest(monkeypatch):
    from repowise.server.chat_datasets import _cache, _store

    monkeypatch.setattr(chat_datasets, "_CACHE_LIMIT", 3)
    _store(("r1", "s1", "files"), [])
    _store(("r2", "s1", "files"), [])
    _store(("r1", "s2", "files"), [])
    # r1's first state is gone; r2 keeps its own.
    assert list(_cache) == [("r2", "s1", "files"), ("r1", "s2", "files")]
    _store(("r3", "s1", "files"), [])
    _store(("r4", "s1", "files"), [])
    assert list(_cache) == [("r1", "s2", "files"), ("r3", "s1", "files"), ("r4", "s1", "files")]


@pytest.fixture
def probe_dataset():
    calls: list[int] = []
    state = {"fail": False}

    async def load(ctx):
        import asyncio

        calls.append(1)
        await asyncio.sleep(0.01)
        if state["fail"]:
            raise RuntimeError("index unreadable")
        return [{"k": "a"}]

    chat_datasets.register_dataset(
        "probe",
        description="test",
        fields={"k": chat_datasets.DatasetField("text", "a key")},
        basis=("inferred", "test"),
        example={"x": "k"},
    )(load)
    yield calls, state
    chat_datasets.datasets._impls.pop("probe")


async def test_concurrent_loads_read_once(chart_index, probe_dataset):
    import asyncio

    calls, _ = probe_dataset
    results = await asyncio.gather(*(chat_datasets.load_rows("probe", None) for _ in range(5)))
    assert len(calls) == 1
    assert all(r.rows == [{"k": "a"}] for r in results)
    assert chat_datasets._locks == {}


async def test_a_failed_load_is_not_cached_and_the_tool_says_so(chart_index, probe_dataset):
    calls, state = probe_dataset
    state["fail"] = True
    result = await run_chart({"dataset": "probe", "x": "k"}, None, TurnViews())
    assert result["error_code"] == "tool_failed" and "index unreadable" in result["error"]
    assert chat_datasets._locks == {}
    state["fail"] = False
    assert "error" not in await run_chart({"dataset": "probe", "x": "k"}, None, TurnViews())
    assert len(calls) == 2
