"""``repowise packages where | export | import`` against a small on-disk index."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.cli.main import cli
from tests.unit.core.inventory_fixture import rows, write_repo

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "advisories"


async def _seed(root: Path) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from repowise.core.persistence.database import init_db, resolve_db_url

    engine = create_async_engine(resolve_db_url(root))
    await init_db(engine)
    async with async_sessionmaker(engine, class_=AsyncSession)() as session:
        session.add_all(rows(root))
        await session.commit()
    await engine.dispose()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    write_repo(tmp_path)
    asyncio.run(_seed(tmp_path))
    return tmp_path


def _invoke(args: list[str]):
    return CliRunner().invoke(cli, args, catch_exceptions=False)


def test_where_json_and_text(repo):
    result = _invoke(["packages", "where", "requests", "--path", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["summary"]["files"] == 4
    assert doc["sites"][0]["path"] == "src/app/util.py"

    text = _invoke(["packages", "where", "pypi:requests", "--path", str(repo)])
    assert text.exit_code == 0, text.output
    assert "src/app/util.py:2" in text.output
    assert "1 reachable" in text.output
    assert "1 individual(s)" in text.output  # counted, never named
    assert "someone" not in text.output


def test_export_writes_cyclonedx(repo, tmp_path):
    out = tmp_path / "bom.json"
    result = _invoke(["packages", "export", "--format", "cyclonedx", "-o", str(out), "--path", str(repo)])
    assert result.exit_code == 0, result.output
    bom = json.loads(out.read_text(encoding="utf-8"))
    assert bom["specVersion"] == "1.6"
    assert {c["name"] for c in bom["components"]} == {"requests", "PyYAML", "pytest"}


def test_import_joins_an_osv_file(repo):
    result = _invoke(
        ["packages", "import", str(FIXTURES / "osv.json"), "--path", str(repo), "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [r["name"] for r in data["matched"]] == ["requests"]

    text = _invoke(["packages", "import", str(FIXTURES / "syft.json"), "--path", str(repo)])
    assert text.exit_code == 0, text.output
    assert "from a syft file" in text.output


def test_import_rejects_an_unknown_file(repo, tmp_path):
    bad = tmp_path / "x.json"
    bad.write_text('{"hello": 1}', encoding="utf-8")
    result = CliRunner().invoke(cli, ["packages", "import", str(bad), "--path", str(repo)])
    assert result.exit_code != 0
    assert "not a Syft, SPDX, CycloneDX or OSV" in result.output


def test_no_index_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    result = CliRunner().invoke(cli, ["packages", "where", "x", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "run `repowise init`" in result.output


def test_report_inventory(repo):
    result = _invoke(["report", "inventory", str(repo), "--format", "json", "--no-workspace"])
    assert result.exit_code == 0, result.output
    doc = json.loads(result.output)
    assert doc["kind"] == "inventory"
    assert [s["id"] for s in doc["sections"]] == ["inventory"]

    md = _invoke(["report", "inventory", str(repo), "--no-workspace"])
    assert md.exit_code == 0, md.output
    assert md.output.startswith("# Inventory: ")
