"""Tests for the pypi (pyproject / requirements) parser."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.external_systems import pypi


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_parses_pep621_dependencies(tmp_path):
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        """
[project]
name = "demo"
dependencies = [
    "fastapi>=0.100",
    "httpx[http2]>=0.27,<1",
]

[project.optional-dependencies]
dev = ["pytest>=7", "ruff>=0.5"]
""",
    )
    records = pypi.parse(pyproject, tmp_path)
    names = {r.name for r in records}
    assert {"fastapi", "httpx", "pytest", "ruff"} <= names

    fastapi = next(r for r in records if r.name == "fastapi")
    assert fastapi.is_dev_dep is False
    assert fastapi.category == "framework"

    pytest_rec = next(r for r in records if r.name == "pytest")
    assert pytest_rec.is_dev_dep is True
    assert pytest_rec.category == "tool"


def test_parses_poetry_layout(tmp_path):
    pyproject = _write(
        tmp_path,
        "pyproject.toml",
        """
[tool.poetry]
name = "demo"

[tool.poetry.dependencies]
python = "^3.11"
django = "^5.0"
stripe = {version = "^7.0"}

[tool.poetry.group.dev.dependencies]
black = "^24"
""",
    )
    records = pypi.parse(pyproject, tmp_path)
    by_name = {r.name: r for r in records}
    assert "django" in by_name and by_name["django"].category == "framework"
    assert "stripe" in by_name and by_name["stripe"].category == "service"
    assert by_name["black"].is_dev_dep is True
    assert "python" not in by_name  # explicit skip


def test_requirements_txt(tmp_path):
    p = _write(
        tmp_path,
        "requirements.txt",
        "# comment\nfastapi==0.110\nrequests>=2  ; python_version > '3'\n-e .\n",
    )
    records = pypi.parse(p, tmp_path)
    names = {r.name for r in records}
    assert names == {"fastapi", "requests"}


def test_malformed_pyproject_returns_empty(tmp_path):
    p = _write(tmp_path, "pyproject.toml", "not [ valid toml")
    assert pypi.parse(p, tmp_path) == []
