"""A fixture-tree namer stands for the test code that may read its fixture tree."""

from __future__ import annotations

from repowise.core.analysis.fixture_readers import fixture_root, with_fixture_readers


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
