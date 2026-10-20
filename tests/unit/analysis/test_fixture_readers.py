"""A fixture-tree namer stands for the test code that may read its fixture tree."""

from __future__ import annotations

import subprocess

import pytest

from repowise.core.analysis.changed_lines import ChangeSet, FileDiff
from repowise.core.analysis.fixture_readers import (
    fixture_root,
    with_data_file_readers,
    with_fixture_readers,
)
from repowise.core.analysis.test_collection import plan_scopes, read_checkout
from repowise.core.analysis.test_selection import (
    SHARED_TEST_DATA_REASON,
    SelectionInput,
    TestSelectionConfig,
    select_tests,
)


def test_a_fixture_root_is_the_directory_under_the_outermost_fixtures_directory() -> None:
    assert fixture_root("tests/fixtures/ts_sample/packages/lib/src/orphan.ts") == (
        "tests/fixtures/ts_sample"
    )
    assert fixture_root("pkg/__fixtures__/app/a.ts") == "pkg/__fixtures__/app"
    assert fixture_root("tests/fixtures/repo/fixtures/inner/a.py") == "tests/fixtures/repo"
    assert fixture_root("tests/Fixtures/repo/a.py") == "tests/Fixtures/repo"
    assert fixture_root("tests/fixtures/data.json") is None
    assert fixture_root("src/app.ts") is None


_TEXTS = {
    "tests/test_ts.py": 'ROOT = FIXTURES / "ts_sample"\n',
    "tests/test_dynamic.py": 'ROOT = HERE / "fixtures" / f"{lang}_sample"\n',
    "tests/test_fstring.py": 'ROOT = HERE / f"ts_sample/{part}"\n',
    "tests/helpers.py": "def trees():\n    return list((HERE / 'FIXTURES').iterdir())\n",
    "tests/test_uses_helper.py": "from tests.helpers import trees\n",
    "tests/conftest.py": "import pytest\n",
    "tests/test_other.py": 'ROOT = "ts_sample_extra"\n',
    "tests/fixtures/ts_sample/b.ts": "// ts_sample fixture\n",
    "tests/web/fixtures/data.ts": "/** Synthetic fixtures. */\nexport const page = 1;\n",
    "tests/test_doc.py": '"""Reads fixtures."""\nx = 1  # fixtures\n',
    "src/app.py": "FIXTURE_WORDS = ('fixtures',)\n",
}
_TEST_CODE = [p for p in _TEXTS if p.startswith("tests/")]
_OTHER = [p for p in _TEXTS if not p.startswith("tests/")]


def _read(path: str) -> str | None:
    return _TEXTS.get(path)


def test_a_fixture_namer_becomes_every_piece_of_test_code_naming_its_tree() -> None:
    namers = {"package.json": ["src/cli.py", "tests/fixtures/ts_sample/a.ts"]}
    out = with_fixture_readers(namers, _TEST_CODE, _OTHER, _read)
    # A helper and a dynamically built tree name are readers too; the helper
    # routes to its importers like any namer. Fixture files are not readers,
    # nor is a mention in a comment or a docstring.
    assert out == {
        "package.json": [
            "src/cli.py",
            "tests/test_ts.py",
            "tests/test_dynamic.py",
            "tests/test_fstring.py",
            "tests/helpers.py",
        ]
    }


def test_trees_sharing_a_name_share_their_readers() -> None:
    namers = {"package.json": ["a/fixtures/ts_sample/x.ts", "b/fixtures/ts_sample/y.ts"]}
    out = with_fixture_readers(namers, _TEST_CODE, _OTHER, _read)["package.json"]
    assert "tests/test_ts.py" in out
    assert not any("fixtures/ts_sample" in p for p in out)


def test_a_tree_no_test_code_names_keeps_its_file_as_the_namer() -> None:
    namers = {"package.json": ["tests/fixtures/go_sample/main.go"]}
    assert with_fixture_readers(namers, ["tests/test_other.py"], [], _read) == namers


def test_a_tree_named_by_code_outside_the_test_trees_keeps_its_full_run() -> None:
    texts = {**_TEXTS, "src/indexer.py": 'SAMPLE = "tests/fixtures/ts_sample"\n'}
    namers = {"package.json": ["tests/fixtures/ts_sample/a.ts"]}
    out = with_fixture_readers(namers, _TEST_CODE, [*_OTHER, "src/indexer.py"], texts.get)
    assert out == namers


def test_grep_narrowing_gives_the_same_readers() -> None:
    namers = {"package.json": ["tests/fixtures/ts_sample/a.ts"]}

    def holding(names):
        return {p for p, t in _TEXTS.items() if any(n.lower() in t.lower() for n in names)}

    narrowed = with_fixture_readers(namers, _TEST_CODE, _OTHER, _read, holding)
    assert narrowed == with_fixture_readers(namers, _TEST_CODE, _OTHER, _read)


# --- a changed fixture data file runs the tests reading it (#4094) -----------------


def test_a_data_file_is_read_by_the_test_code_naming_it_or_its_tree() -> None:
    files = ["tests/fixtures/ts_sample/b.json", "tests/fixtures/cases.json"]
    namers = {"tests/fixtures/cases.json": ["tests/test_other.py"]}
    out = with_data_file_readers(files, namers, _TEST_CODE, _OTHER, _read)
    # A file directly in the fixtures directory has no tree: only a name reads it.
    assert out["tests/fixtures/cases.json"] == ["tests/test_other.py"]
    assert out["tests/fixtures/ts_sample/b.json"] == [
        "tests/test_ts.py",
        "tests/test_dynamic.py",
        "tests/test_fstring.py",
        "tests/helpers.py",
    ]


def test_a_data_file_no_test_code_names_keeps_its_full_run() -> None:
    assert with_data_file_readers(["tests/fixtures/x.json"], {}, _TEST_CODE, _OTHER, _read) == {}


def test_a_data_file_named_by_production_code_keeps_its_full_run() -> None:
    namers = {"tests/fixtures/cases.json": ["tests/test_other.py", "src/app.py"]}
    assert (
        with_data_file_readers(["tests/fixtures/cases.json"], namers, _TEST_CODE, _OTHER, _read)
        == {}
    )


def test_a_data_file_in_a_tree_named_by_production_code_keeps_its_full_run() -> None:
    texts = {**_TEXTS, "src/indexer.py": 'SAMPLE = "tests/fixtures/ts_sample"\n'}
    out = with_data_file_readers(
        ["tests/fixtures/ts_sample/b.json"],
        {"tests/fixtures/ts_sample/b.json": ["tests/test_other.py"]},
        _TEST_CODE,
        [*_OTHER, "src/indexer.py"],
        texts.get,
    )
    assert out == {}


_REPO = {
    "pkg/core.py": "def add(a, b):\n    return a + b\n",
    "pkg/loader.py": 'DATA = "tests/fixtures/prod.json"\n',
    "tests/test_core.py": 'CASES = json.loads((HERE / "fixtures" / "cases.json").read_text())\n',
    "tests/test_other.py": "from pkg.core import add\n",
    "tests/test_tree_a.py": 'ROOT = FIXTURES / "golden"\n',
    "tests/test_tree_b.py": 'ROOT = HERE / "fixtures" / "golden"\n',
    "tests/test_helper_user.py": "from tests.helpers import load\n",
    "tests/helpers.py": 'def load():\n    return open("loose.json")\n',
    "tests/orphan_helpers.py": 'def load():\n    return open("stray.json")\n',
    "tests/fixtures/cases.json": "{}",
    "tests/fixtures/orphan.json": "{}",
    "tests/fixtures/prod.json": "{}",
    "tests/fixtures/loose.json": "{}",
    "tests/fixtures/stray.json": "{}",
    "tests/fixtures/golden/a.json": "{}",
    "Cargo.lock": "",
}
_KNOWN = sorted(p for p in _REPO if p.startswith("tests/test_"))


def _selection(tmp_path, changed, config=None, importers=None, rows=()):
    for path, text in _REPO.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    config = config or TestSelectionConfig()
    change = ChangeSet(
        files={p: FileDiff(path=p) for p in changed}, deleted=set(), label="t", base=None, head=None
    )
    plan = plan_scopes(change, config, read_checkout(tmp_path))
    # What the graph answers for a route that is itself a test: that test.
    inferred = [
        {"source_file": r, "test_file": r, "via": "changed-test"}
        for r in plan.routes
        if r in _KNOWN
    ]
    inferred += [{"source_file": s, "test_file": t, "via": "import-graph"} for s, t in rows]
    tiers = {
        "covered": {},
        "inferred": inferred,
        "unknown": [],
        "helper_importers": importers or {},
    }
    inp = SelectionInput(list(changed), (), tiers, config, scopes=plan.scopes, known_tests=_KNOWN)
    return select_tests(inp)


def test_a_fixture_file_named_by_one_test_selects_only_that_test(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/cases.json"])
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("tests/test_core.py",)
    assert sel.basis == {"tests/fixtures/cases.json": "fixture-readers"}
    why = sel.why["tests/test_core.py"]
    assert why.startswith("tests/fixtures/cases.json changed (fixture-readers:")
    assert "read by tests/test_core.py" in why


def test_a_fixture_file_in_a_tree_named_by_two_tests_selects_both(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/golden/a.json"])
    assert not sel.run_all, sel.reasons
    # ``test_core.py`` names the fixtures directory itself (``HERE / "fixtures"``), which
    # lists every tree in it; ``test_other.py`` names neither and stays out.
    assert sorted(sel.test_files) == [
        "tests/test_core.py",
        "tests/test_tree_a.py",
        "tests/test_tree_b.py",
    ]


def test_a_fixture_file_no_test_names_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/orphan.json"])
    assert sel.run_all
    assert sel.reasons[0] == f"tests/fixtures/orphan.json changed: {SHARED_TEST_DATA_REASON}."
    assert sel.basis == {"tests/fixtures/orphan.json": "full-run"}


def test_a_fixture_file_named_by_production_code_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/prod.json"])
    assert sel.run_all
    assert sel.reasons[0] == f"tests/fixtures/prod.json changed: {SHARED_TEST_DATA_REASON}."


def test_a_fixture_read_by_a_helper_a_test_imports_selects_that_test(tmp_path) -> None:
    importers = {"tests/helpers.py": ["tests/test_helper_user.py"]}
    rows = [("tests/helpers.py", "tests/test_helper_user.py")]
    sel = _selection(tmp_path, ["tests/fixtures/loose.json"], importers=importers, rows=rows)
    assert not sel.run_all, sel.reasons
    assert sel.test_files == ("tests/test_helper_user.py",)


def test_a_fixture_read_by_a_helper_no_test_imports_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/stray.json"])
    assert sel.run_all
    assert sel.reasons[0].startswith(
        "tests/fixtures/stray.json changed; tests/orphan_helpers.py is a test helper no test imports"
    )


def test_a_fixture_changed_with_a_build_file_still_runs_everything(tmp_path) -> None:
    sel = _selection(tmp_path, ["tests/fixtures/cases.json", "Cargo.lock"])
    assert sel.run_all
    assert any(r.startswith("Cargo.lock changed:") for r in sel.reasons)


def test_a_configured_full_run_still_wins_over_the_readers(tmp_path) -> None:
    config = TestSelectionConfig(full_run_on=("tests/fixtures/cases.json",))
    sel = _selection(tmp_path, ["tests/fixtures/cases.json"], config)
    assert sel.run_all
    assert sel.basis == {"tests/fixtures/cases.json": "full-run"}


@pytest.mark.parametrize("path", ["tests/fixtures/sample.py", "tests/fixtures/mod/handler.ts"])
def test_fixture_code_keeps_its_full_run(tmp_path, path) -> None:
    """A test may import fixture code by module name, which no file name search sees."""
    sel = _selection(tmp_path, [path])
    assert sel.run_all
