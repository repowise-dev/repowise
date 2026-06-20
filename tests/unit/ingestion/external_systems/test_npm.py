"""Tests for the npm package.json parser."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion.external_systems import npm


def _write(tmp_path: Path, rel: str, data: dict) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_parses_dependencies_and_dev_dependencies(tmp_path):
    manifest = _write(
        tmp_path,
        "package.json",
        {
            "dependencies": {"react": "^18.0.0", "@aws-sdk/client-s3": "^3.0.0"},
            "devDependencies": {"vitest": "^1.0.0"},
        },
    )
    records = npm.parse(manifest, tmp_path)
    names = {r.name for r in records}
    assert names == {"react", "@aws-sdk/client-s3", "vitest"}

    react = next(r for r in records if r.name == "react")
    assert react.version == "^18.0.0"
    assert react.is_dev_dep is False
    assert react.category == "framework"
    assert react.ecosystem == "npm"

    aws = next(r for r in records if r.name == "@aws-sdk/client-s3")
    assert aws.category == "service"

    vitest = next(r for r in records if r.name == "vitest")
    assert vitest.is_dev_dep is True
    assert vitest.category == "tool"


def test_io_kind_is_wired_through_the_parser(tmp_path):
    manifest = _write(
        tmp_path,
        "package.json",
        {
            "dependencies": {
                "axios": "^1.0.0",
                "@prisma/client": "^5.0.0",
                "react": "^18.0.0",
            },
        },
    )
    records = {r.name: r for r in npm.parse(manifest, tmp_path)}
    assert records["axios"].io_kind == "network"
    assert records["@prisma/client"].io_kind == "db"
    # An untyped dependency carries None, not a guess.
    assert records["react"].io_kind is None


def test_handles_malformed_json_without_raising(tmp_path):
    p = tmp_path / "package.json"
    p.write_text("{ not valid", encoding="utf-8")
    assert npm.parse(p, tmp_path) == []


def test_skips_workspace_members(tmp_path):
    # Root manifest declares workspaces + a "dep" that is actually a workspace.
    _write(
        tmp_path,
        "packages/core/package.json",
        {"name": "@org/core"},
    )
    root = _write(
        tmp_path,
        "package.json",
        {
            "workspaces": ["packages/*"],
            "dependencies": {"@org/core": "workspace:*", "react": "^18"},
        },
    )
    records = npm.parse(root, tmp_path)
    names = {r.name for r in records}
    assert names == {"react"}


def test_declared_in_is_repo_relative_posix(tmp_path):
    nested = _write(
        tmp_path,
        "packages/web/package.json",
        {"dependencies": {"next": "^14"}},
    )
    records = npm.parse(nested, tmp_path)
    assert records[0].declared_in == "packages/web/package.json"
