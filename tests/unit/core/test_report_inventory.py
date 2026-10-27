"""The ``inventory`` report section over the package-usage fixture index."""

from __future__ import annotations

from repowise.core.components.resolve import repo_wide_when_flat
from repowise.core.report.build import available_sections, collect_repo
from repowise.core.report.model import ReportContext
from tests.unit.core.inventory_fixture import REPO, session  # noqa: F401  (fixture)


async def test_inventory_report_section(session, tmp_path):  # noqa: F811
    sections = [(n, s) for n, s in available_sections() if n == "inventory"]
    _entry, (section,) = await collect_repo(ReportContext(session, REPO, tmp_path), sections)
    checks = {c["name"]: c for c in section["checks"]}
    assert checks["inventory"]["basis"] == "measured"
    assert "declared, no linked import: pypi:pyyaml" in checks["inventory"]["details"]

    summary = checks["inventory:components"]
    assert summary["score"] is None  # descriptive, never scored
    assert "with a declared owner team: 2" in summary["reason"]
    # Every component's row stays in the JSON data.
    app = summary["data"]["components"]["app"]
    assert app["entry_points"] == ["src/app/main.py"]
    assert app["packages"] == [{"package_key": "pypi:requests", "files": 1}]

    # Only components with no declared owner team get an item of their own.
    assert "inventory:app" not in checks and "inventory:jobs" not in checks
    # src/ and tests/ are layout, so the rest of the repo is one derived component.
    src = checks[f"inventory:{repo_wide_when_flat([], ['x.py'], tmp_path.name)[0].name}"]
    assert "no declared owner team" in src["reason"]
    assert "package: pypi:requests (1 file(s))" in src["details"]
