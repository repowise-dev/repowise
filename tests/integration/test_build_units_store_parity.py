"""An update that adds and removes workspace members and deletes a file stores what init would."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.integration.test_update_store_parity import _INIT, _commit, _git, _invoke, _write

_SEED = {
    "package.json": json.dumps({"name": "root", "private": True, "workspaces": ["packages/*"]}),
    "packages/a/package.json": json.dumps({"name": "@x/a"}),
    "packages/a/index.js": "export const a = 1;\n",
    "packages/b/package.json": json.dumps({"name": "@x/b", "dependencies": {"@x/a": "*"}}),
    "packages/b/index.js": "import { a } from '@x/a';\nexport const b = a;\n",
    "packages/b/extra.js": "export const extra = 2;\n",
    "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
    "crates/c/Cargo.toml": '[package]\nname = "c"\n',
    "crates/c/src/lib.rs": "pub fn c() -> i32 { 1 }\n",
}


def _units(repo: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(repo / ".repowise" / "wiki.db")) as conn:
        return {
            "build_units": sorted(
                conn.execute("SELECT unit_id, ecosystem, name, root, manifest, source FROM build_units")
            ),
            "build_unit_deps": sorted(conn.execute("SELECT unit_id, dep_unit_id FROM build_unit_deps")),
            "build_unit_files": sorted(conn.execute("SELECT path, unit_id FROM build_unit_files")),
        }


@pytest.mark.parametrize("update_args", [("--index-only",), ()], ids=["index-only", "docs"])
def test_update_stores_the_units_a_fresh_init_stores(
    tmp_path: Path, monkeypatch, update_args: tuple[str, ...]
) -> None:
    incremental = tmp_path / "incremental" / "repo"
    incremental.mkdir(parents=True)
    _write(incremental, _SEED)
    _git(incremental, "init", "-q")
    _commit(incremental, "seed")
    runner = CliRunner()
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{incremental / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, incremental, *_INIT)
    before = _units(incremental)
    assert ("packages/b/extra.js", "npm:@x/b") in before["build_unit_files"]

    # A new member depending on an old one, a removed member and a deleted file.
    _write(
        incremental,
        {
            "packages/d/package.json": json.dumps({"name": "@x/d", "dependencies": {"@x/b": "*"}}),
            "packages/d/index.js": "import { b } from '@x/b';\nexport const d = b;\n",
        },
    )
    (incremental / "packages/b/extra.js").unlink()
    shutil.rmtree(incremental / "packages/a")
    _commit(incremental, "feat: add d, drop a and extra")
    _invoke(runner, incremental, "update", *update_args, "--no-workspace", "--no-agents")

    control = tmp_path / "control" / "repo"
    shutil.copytree(incremental, control, ignore=shutil.ignore_patterns(".repowise"))
    monkeypatch.setenv(
        "REPOWISE_DB_URL", f"sqlite+aiosqlite:///{control / '.repowise' / 'wiki.db'}"
    )
    _invoke(runner, control, *_INIT)

    expected = _units(control)
    assert ("npm:@x/b", "npm:@x/a") in before["build_unit_deps"]
    unit_ids = {u[0] for u in expected["build_units"]}
    assert {"npm:@x/b", "npm:@x/d", "cargo:c"} <= unit_ids and "npm:@x/a" not in unit_ids
    assert not [d for d in expected["build_unit_deps"] if "npm:@x/a" in d]
    assert ("npm:@x/d", "npm:@x/b") in expected["build_unit_deps"]
    assert ("packages/d/index.js", "npm:@x/d") in expected["build_unit_files"]
    assert ("crates/c/src/lib.rs", "cargo:c") in expected["build_unit_files"]
    assert _units(incremental) == expected
