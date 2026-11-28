"""Stored advisories tiered against the index: the read behind every advisory surface."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from repowise.core.ingestion.external_calls import ExternalCallSite
from repowise.core.inventory.advisories import join_usage, load_advisories
from repowise.core.inventory.advisory_tiers import (
    called_advisories_in_files,
    check_package,
    split_version,
    tier_advisories,
)
from repowise.core.persistence.crud.external_call_sites import (
    replace_external_call_sites,
    stamp_external_call_sites,
)
from repowise.core.persistence.models import GraphEdge
from tests.unit.core.inventory_fixture import REPO, session  # noqa: F401

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "advisories"


def _call(path: str, line: int, caller: str, module: str = "requests", symbol: str = "get"):
    return ExternalCallSite(
        file_path=path,
        line=line,
        caller_symbol_id=caller,
        ecosystem="pypi",
        module=module,
        package=module.split(".")[0],
        external_node=f"external:{module}",
        local_name=symbol,
        symbol_path=symbol,
        binding="named",
    )


async def _seed_calls(session, *, complete: bool = True) -> None:  # noqa: F811
    # main.py's top-level code calls util.fetch; legacy.old is never called.
    session.add(
        GraphEdge(
            repository_id=REPO,
            source_node_id="src/app/main.py::__module__",
            target_node_id="src/app/util.py::fetch",
            edge_type="calls",
        )
    )
    calls = [
        _call("src/app/util.py", 5, "src/app/util.py::fetch"),
        _call("src/old/legacy.py", 3, "src/old/legacy.py::old"),
    ]
    by_file: dict[str, list] = {}
    for c in calls:
        by_file.setdefault(c.file_path, []).append(c)
    await replace_external_call_sites(
        session, REPO, by_file, scope=None, live_paths=list(by_file), complete=complete
    )
    if not complete:
        await stamp_external_call_sites(session, REPO, None)
    await session.commit()


async def _import(session, tmp_path, name: str) -> None:  # noqa: F811
    await join_usage(session, REPO, tmp_path, str(FIXTURES / name), store=True)
    await session.commit()


async def test_no_document_is_none(session, tmp_path):  # noqa: F811
    assert await tier_advisories(session, REPO, tmp_path) is None
    assert await called_advisories_in_files(session, REPO, tmp_path, ["src/app/util.py"]) == {}


async def test_a_reached_call_makes_the_package_called(session, tmp_path):  # noqa: F811
    await _seed_calls(session)
    await _import(session, tmp_path, "osv-scanner.json")
    rows = await tier_advisories(session, REPO, tmp_path)
    by_pkg = {r.package: r for r in rows}
    requests = by_pkg["requests"]
    assert requests.tier.tier == "package_called"
    assert requests.called
    # The unreached legacy call is not evidence; the reached one is.
    assert requests.sites() == ["src/app/util.py:5"]
    assert requests.as_dict()["sites"] == [
        {"path": "src/app/util.py", "line": 5, "symbol": "requests.get"}
    ]
    # Named by the document, imported nowhere, may be transitive: shown as
    # unknown, never as a tier below imported.
    assert by_pkg["jinja2"].tier.tier == "indirect_unknown"
    assert by_pkg["jinja2"].shown_tier == "unknown"
    jinja2 = by_pkg["jinja2"].as_dict()
    assert (jinja2["tier"], jinja2["basis"], jinja2["sites"]) == (
        "unknown",
        "no_shipping_import",
        [],
    )
    assert by_pkg["jinja2"].rank == 1
    assert rows[0].package == "requests"  # strongest tier first


async def test_npm_calls_are_not_claimed(session, tmp_path):  # noqa: F811
    """A stored npm call never makes a package called: npm stays at imported."""
    import json

    from repowise.core.persistence.models import ExternalSystem, GraphNode

    session.add_all(
        [
            ExternalSystem(
                id=10, repository_id=REPO, name="lodash", ecosystem="npm",
                version="4.17.20", declared_in="package.json",
            ),
            GraphNode(
                repository_id=REPO, node_id="src/app/web.ts", node_type="file",
                language="typescript", is_entry_point=True,
            ),
            GraphNode(
                repository_id=REPO, node_id="external:lodash", node_type="external",
                external_system_id=10,
            ),
            GraphEdge(
                repository_id=REPO, source_node_id="src/app/web.ts",
                target_node_id="external:lodash", imported_names_json='["merge"]',
                edge_type="imports",
            ),
        ]
    )  # fmt: skip
    (tmp_path / "src/app/web.ts").write_text("import { merge } from 'lodash';\n", encoding="utf-8")
    call = ExternalCallSite(
        file_path="src/app/web.ts", line=2, caller_symbol_id="src/app/web.ts::__module__",
        ecosystem="npm", module="lodash", package="lodash", external_node="external:lodash",
        local_name="merge", symbol_path="merge", binding="named",
    )  # fmt: skip
    await replace_external_call_sites(
        session, REPO, {"src/app/web.ts": [call]}, scope=None, live_paths=["src/app/web.ts"]
    )
    doc = {
        "results": [
            {
                "packages": [
                    {
                        "package": {"name": "lodash", "version": "4.17.20", "ecosystem": "npm"},
                        "vulnerabilities": [{"id": "GHSA-35jh-r3h4-6jhm"}],
                    }
                ]
            }
        ]
    }
    (tmp_path / "npm.json").write_text(json.dumps(doc), encoding="utf-8")
    await join_usage(session, REPO, tmp_path, str(tmp_path / "npm.json"), store=True)
    await session.commit()
    (row,) = await tier_advisories(session, REPO, tmp_path)
    assert (row.shown_tier, row.tier.basis) == ("imported", "no_call_records")
    assert await called_advisories_in_files(session, REPO, tmp_path, ["src/app/web.ts"]) == {}


async def test_incomplete_call_table_stays_imported(session, tmp_path):  # noqa: F811
    await _seed_calls(session, complete=False)
    await _import(session, tmp_path, "osv-scanner.json")
    rows = await tier_advisories(session, REPO, tmp_path)
    requests = next(r for r in rows if r.package == "requests")
    assert (requests.tier.tier, requests.tier.basis) == ("imported", "no_call_records")


async def test_a_version_outside_the_range_is_dropped(session, tmp_path):  # noqa: F811
    await _seed_calls(session)
    await _import(session, tmp_path, "osv.json")
    # requests is declared ">=2.31": a range, so the verdict stays undecided.
    rows = await tier_advisories(session, REPO, tmp_path)
    assert [(r.package, r.affected) for r in rows] == [("requests", None)]


async def test_called_in_files_only_names_files_whose_call_is_reached(session, tmp_path):  # noqa: F811
    await _seed_calls(session)
    await _import(session, tmp_path, "osv-scanner.json")
    files = ["src/app/util.py", "src/old/legacy.py", "src/jobs/worker.py"]
    found = await called_advisories_in_files(session, REPO, tmp_path, files)
    assert list(found) == ["src/app/util.py"]
    assert [r.advisory.id for r in found["src/app/util.py"]] == ["GHSA-9wx4-h78v-vm56"]
    assert found["src/app/util.py"][0].sites() == ["src/app/util.py:5"]


async def test_unclaimed_calls_read_no_advisory(session, tmp_path, monkeypatch):  # noqa: F811
    """A Java call is never claimed, so its file costs the one read of its calls."""
    from repowise.core.inventory import advisory_tiers

    call = replace(
        _call("src/App.java", 4, "src/App.java::App::run", "com.acme.Client", "Client.get"),
        ecosystem="maven",
        package="",
    )
    await replace_external_call_sites(
        session, REPO, {call.file_path: [call]}, scope=None, live_paths=[call.file_path]
    )
    await session.commit()

    async def unread(*args, **kwargs):
        raise AssertionError("advisories read")

    monkeypatch.setattr(advisory_tiers, "load_advisories", unread)
    assert await called_advisories_in_files(session, REPO, tmp_path, ["src/App.java"]) == {}


async def test_check_package(session, tmp_path):  # noqa: F811
    assert check_package(None, "pypi", "requests", "2.31.0").verdict == "unknown_no_source"
    await _import(session, tmp_path, "osv.json")
    stored = await load_advisories(session, REPO)
    hit = check_package(stored, "pypi", "requests", "2.31.0")
    assert hit.verdict == "has_advisories"
    assert hit.as_dict()["advisories"] == [
        {"id": "GHSA-9wx4-h78v-vm56", "affected": True, "fix": "2.32.0"}
    ]
    assert check_package(stored, "pypi", "Requests", "2.32.0").verdict == "ok"
    assert check_package(stored, "pypi", "flask", "3.0").verdict == "unknown_no_source"
    undecided = check_package(stored, "pypi", "requests", None)
    assert undecided.verdict == "has_advisories"
    assert undecided.advisories[0][1] is None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("requests@2.31.0", ("requests", "2.31.0")),
        ("pypi:requests@2.31.0", ("pypi:requests", "2.31.0")),
        ("@scope/pkg", ("@scope/pkg", None)),
        ("npm:@scope/pkg", ("npm:@scope/pkg", None)),
        ("npm:@scope/pkg@1.0.0", ("npm:@scope/pkg", "1.0.0")),
        ("requests@", ("requests@", None)),
        ("requests", ("requests", None)),
    ],
)
def test_split_version(query, expected):
    assert split_version(query) == expected
